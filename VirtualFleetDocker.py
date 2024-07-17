import os
import sys
import json
import time
import socket
import shutil
import logging
import pathlib
import argparse
import multiprocessing
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

import docker
from docker import DockerClient
from docker.models.containers import Container


NUMBER_OF_LOG_LAST_LINES_TO_SHOW = 5
CONFIG_DIR = "./config"
TMP_CONFIG_DIR = os.path.abspath(os.path.join(CONFIG_DIR, "tmp-configs"))
EXTERNAL_SERVER_CONFIG = os.path.abspath(os.path.join(CONFIG_DIR, "external-server/config.json"))
MODULE_GATEWAY_CONFIG = os.path.abspath(os.path.join(CONFIG_DIR, "module-gateway/config.json"))
VERNEMQ_CONFIG = os.path.abspath(os.path.join(CONFIG_DIR, "vernemq"))
LOGS_DIR = "logs"


@dataclass
class Vehicle:
    company: str
    name: str
    id: str = field(init=False)

    def __post_init__(self):
        self.id = f"{self.company}-{self.name}"


@dataclass
class Settings:
    vehicles: list[dict[str, str]]
    vehicles_instances: list[Vehicle] = field(init=False)
    stop_running_containers: bool
    external_server_docker_image: str
    external_server_docker_tag: str
    external_server_docker_id: str = field(init=False)
    gateway_docker_image: str
    gateway_docker_tag: str
    gateway_docker_id: str = field(init=False)
    vehicle_docker_image: str
    vehicle_docker_tag: str
    vehicle_docker_id: str = field(init=False)
    vernemq_docker_image: str
    vernemq_docker_tag: str
    vernemq_docker_id: str = field(init=False)
    start_port: int

    def __post_init__(self):
        self.external_server_docker_id = (
            f"{self.external_server_docker_image}:{self.external_server_docker_tag}"
        )
        self.gateway_docker_id = f"{self.gateway_docker_image}:{self.gateway_docker_tag}"
        self.vehicle_docker_id = f"{self.vehicle_docker_image}:{self.vehicle_docker_tag}"
        self.vernemq_docker_id = f"{self.vernemq_docker_image}:{self.vernemq_docker_tag}"
        self.vehicles_instances = [Vehicle(**vehicle) for vehicle in self.vehicles]


running_containers: list[Container] = []
container_id_name_dictionary: dict[str, str] = {}


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=os.path.abspath(os.path.join(CONFIG_DIR, "virtual-fleet-config.json")),
        help="path to json configuration file",
    )
    return parser.parse_args()


def check_vehicles_uniqueness(vehicles: list[Vehicle]) -> None:
    vehicle_ids = set()
    for vehicle in vehicles:
        vehicle_id = f"{vehicle.id}"
        if vehicle_id in vehicle_ids:
            raise ValueError(f"Vehicle {vehicle_id} is not unique")
        vehicle_ids.add(vehicle_id)


def create_config_files(vehicle: Vehicle) -> None:
    with open(os.path.abspath(EXTERNAL_SERVER_CONFIG)) as file:
        config = json.load(file)
    config["company_name"] = vehicle.company
    config["car_name"] = vehicle.name
    tmp_config_path = os.path.join(
        TMP_CONFIG_DIR,
        f"{vehicle.id}-external-server-config.json",
    )
    with open(os.path.abspath(tmp_config_path), "w") as file:
        json.dump(config, file, indent=4)

    with open(os.path.abspath(MODULE_GATEWAY_CONFIG)) as file:
        config = json.load(file)
    config["external-connection"]["company"] = vehicle.company
    config["external-connection"]["vehicle-name"] = vehicle.name
    tmp_config_path = os.path.join(TMP_CONFIG_DIR, f"{vehicle.id}-gateway-config.json")
    with open(os.path.abspath(tmp_config_path), "w") as file:
        json.dump(config, file, indent=4)


def stop_running_containers(image_names: list[str]) -> None:
    """
    Stops and removes Docker containers based on the provided image names.
    """
    if not image_names:
        return
    docker_client = docker.from_env()
    for container in docker_client.containers.list():
        if container.image.tags and any(
            image_name in container.image.tags[0] for image_name in image_names
        ):
            logging.info(
                f"Stopping and removing container {container.image.tags[0]} with id {container.short_id}"
            )
            container.stop()
            container.remove()
    docker_client.close()


def is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) != 0


def initialize_program(settings: Settings) -> None:
    if settings.stop_running_containers:
        stop_running_containers(
            [
                f"{settings.external_server_docker_image}:",
                f"{settings.gateway_docker_image}:",
                f"{settings.vehicle_docker_image}:",
                f"{settings.vernemq_docker_image}:",
            ]
        )
    check_vehicles_uniqueness(settings.vehicles_instances)
    os.makedirs(os.path.abspath(TMP_CONFIG_DIR), mode=0o755, exist_ok=True)

    for vehicle in settings.vehicles_instances:
        create_config_files(vehicle)


def run_program(settings: Settings) -> None:
    client = docker.from_env()
    port = settings.start_port
    logging.info("Starting docker containers")
    start_mqtt_broker_container(client, settings)

    for vehicle in settings.vehicles_instances:
        while not is_port_available(port):
            logging.info(f"Port {port} is not available, trying next one")
            port += 1

        if not (1024 < port < 65535):
            raise ValueError("Ports out of range, it has to be: 1024 < my port < 65535")

        logging.info(f"Starting vehicle {vehicle.name} with port {port}")
        start_containers(client, settings, vehicle, port)
        port += 1

    logging.info("Start was successful")
    end = False
    while not end:
        for container in running_containers:
            container.reload()
            if not container.attrs["State"]["Running"]:
                container_name = container_id_name_dictionary[container.short_id]
                logging.info(
                    f"Error, container {container_name} with id {container.short_id} is not running, app will exit, container output: \n ==== \n{container.logs(tail=NUMBER_OF_LOG_LAST_LINES_TO_SHOW).decode()} \n ==== \n"
                )
                end = True
        if not end:  # don't want to wait 5 seconds, if containers crash in the beginning
            time.sleep(5)
    stop_containers()
    remove_tmp_config_files()


def get_container_if_running(docker_client: DockerClient, image_name: str) -> Container | None:
    for container in docker_client.containers.list():
        if image_name in container.image.tags:
            return container
    return None


def start_mqtt_broker_container(docker_client: DockerClient, settings: Settings):
    mqtt_broker_container = get_container_if_running(docker_client, settings.vernemq_docker_id)
    if mqtt_broker_container:
        logging.info(
            f"Using existing mqtt broker docker container with id {mqtt_broker_container.short_id}"
        )
        return

    mqtt_broker_container = docker_client.containers.run(
        settings.vernemq_docker_id,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(VERNEMQ_CONFIG): {"bind": "/vernemq/etc", "mode": "ro"},
            os.path.abspath(LOGS_DIR): {"bind": "/vernemq/log", "mode": "rw"},
        },
    )

    logging.info(f"Started a mqtt broker docker container with id {mqtt_broker_container.short_id}")
    container_id_name_dictionary[mqtt_broker_container.short_id] = "mqtt-broker"
    running_containers.append(mqtt_broker_container)


def start_containers(
    docker_client: docker.DockerClient, settings: Settings, vehicle: Vehicle, port: int
):

    external_server_container = docker_client.containers.run(
        settings.external_server_docker_id,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(TMP_CONFIG_DIR): {
                "bind": "/home/bringauto/config",
                "mode": "ro",
            },
            os.path.abspath(LOGS_DIR): {"bind": "/home/bringauto/log", "mode": "rw"},
        },
        entrypoint=[
            "python3",
            "/home/bringauto/external_server/external_server_main.py",
            f"--config=/home/bringauto/config/{vehicle.id}-external-server-config.json",
        ],
    )
    logging.info(
        f"Started an external server docker container with id {external_server_container.short_id}"
    )
    container_id_name_dictionary[external_server_container.short_id] = (
        f"{vehicle.id}-external-server"
    )
    running_containers.append(external_server_container)

    gateway_container = docker_client.containers.run(
        settings.gateway_docker_id,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(TMP_CONFIG_DIR): {
                "bind": "/home/bringauto/config",
                "mode": "ro",
            },
            os.path.abspath(LOGS_DIR): {"bind": "/gateway/log", "mode": "rw"},
        },
        entrypoint=[
            "/home/bringauto/module-gateway/bin/module-gateway-app",
            f"--config-path=/home/bringauto/config/{vehicle.id}-gateway-config.json",
            f"--port={port}",
            "--verbose",
        ],
    )
    logging.info(f"Started a gateway docker container with id {gateway_container.short_id}")
    container_id_name_dictionary[gateway_container.short_id] = f"{vehicle.id}-module-gateway"
    running_containers.append(gateway_container)

    vehicle_container = docker_client.containers.run(
        settings.vehicle_docker_id,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(os.path.join(CONFIG_DIR, "virtual-vehicle")): {
                "bind": "/virtual-vehicle-utility/config",
                "mode": "ro",
            },
            os.path.abspath(LOGS_DIR): {
                "bind": "/virtual-vehicle-utility/log",
                "mode": "rw",
            },
        },
        entrypoint=[
            "/virtual-vehicle-utility/bin/virtual-vehicle-utility",
            "--config=/virtual-vehicle-utility/config/config.json",
            "--verbose",
            "--module-gateway-ip=0.0.0.0",
            f"--module-gateway-port={port}",
        ],
    )

    logging.info(f"Started a vehicle docker container with id {vehicle_container.short_id}")
    container_id_name_dictionary[vehicle_container.short_id] = f"{vehicle.id}-virtual-vehicle"
    running_containers.append(vehicle_container)


def stop_and_remove_container(container: Container, log_dir: pathlib.Path):
    container_name = container_id_name_dictionary[container.short_id]
    logging.info(
        f"Stopping container {container_name} with id {container.short_id} [{running_containers.index(container) + 1}/{len(running_containers)}]"
    )
    try:
        log_filename = f"{container_name}-{container.short_id}.log"
        with open(log_dir / log_filename, "w") as text_file:
            text_file.write(container.logs().decode())
        container.stop()
        container.remove()
    except docker.errors.NotFound:
        logging.error(f"Container {container_name} with id {container.short_id} was not found")
    except docker.errors.APIError as e:
        logging.error(
            f"API error while stopping container {container_name} with id {container.short_id}: {str(e)}. Retrying..."
        )
        try:
            container.stop()
            container.remove()
        except docker.errors.APIError as retry_e:
            logging.error(f"Retry failed: {str(retry_e)}")


def stop_containers():
    log_dir = pathlib.Path(LOGS_DIR)
    if log_dir.exists():
        shutil.rmtree(log_dir, ignore_errors=True)
    log_dir.mkdir()

    # run it with 75% of available cores but at least 1
    num_workers = max(1, int(multiprocessing.cpu_count() * 0.75))
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(stop_and_remove_container, container, log_dir)
            for container in running_containers
        ]
        for future in futures:
            future.result()  # Wait for all futures to complete


def remove_tmp_config_files():
    try:
        shutil.rmtree(TMP_CONFIG_DIR, ignore_errors=True)
    except OSError as e:
        logging.error(f"Error while removing tmp-configs directory {TMP_CONFIG_DIR}: {str(e)}")


def exit_gracefully():
    logging.info("Stopping containers, removing tmp-configs directory and exiting...")
    try:
        stop_containers()
        remove_tmp_config_files()
        sys.exit(0)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        args = parse_arguments()

        with open(args.config) as file:
            settings = Settings(**json.load(file))

        initialize_program(settings)
        run_program(settings)

    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt.")
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
    finally:
        exit_gracefully()
