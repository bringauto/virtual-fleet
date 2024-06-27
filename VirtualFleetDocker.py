import argparse
import json
import logging
import os
import pathlib
import shutil
import signal
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import docker

LOG_LAST_LINES = 5
CONFIG_PATH = "./config"
TMP_CONFIG_PATH = os.path.join(CONFIG_PATH, "tmp-configs")
EXTERNAL_SERVER_CONFIG = os.path.join(CONFIG_PATH, "external-server/config.json")
MODULE_GATEWAY_CONFIG = os.path.join(CONFIG_PATH, "module-gateway/config.json")
VERNEMQ_CONFIG = os.path.join(CONFIG_PATH, "vernemq")
LOGS_DIR = "logs"
runningContainers = []

class FileDoesntExistException(Exception):
    pass

class PortOutOfRangeException(Exception):
    def __init__(self, message="Ports out of range, it has to be: 1024 < my port < 65535"):
        super().__init__(message)

def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.path.join(CONFIG_PATH, "virtual-fleet-config.json"), help="path to json configuration file")
    return parser.parse_args()

def check_paths(arguments):
    if not os.path.exists(arguments.config):
        raise FileDoesntExistException(f"Json file {arguments.config} doesn't exist")

def is_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) != 0

def check_vehicles_uniqueness(vehicles):
    vehicle_names = set()
    for vehicle in vehicles:
        vehicle_id = f"{vehicle['company']}-{vehicle['name']}"
        if vehicle_id in vehicle_names:
            raise ValueError(f"Vehicle {vehicle_id} is not unique")
        vehicle_names.add(vehicle_id)

def create_config_files(vehicle):
    with open(os.path.abspath(EXTERNAL_SERVER_CONFIG), "r") as file:
        config = json.load(file)
    config["company_name"] = vehicle["company"]
    config["car_name"] = vehicle["name"]
    tmp_config_address = os.path.join(TMP_CONFIG_PATH, f"{vehicle['company']}-{vehicle['name']}-external-server-config.json")
    with open(os.path.abspath(tmp_config_address), "w") as file:
        json.dump(config, file, indent=4)

    with open(os.path.abspath(MODULE_GATEWAY_CONFIG), "r") as file:
        config = json.load(file)
    config["external-connection"]["company"] = vehicle["company"]
    config["external-connection"]["vehicle-name"] = vehicle["name"]
    tmp_config_address = os.path.join(TMP_CONFIG_PATH, f"{vehicle['company']}-{vehicle['name']}-gateway-config.json")
    with open(os.path.abspath(tmp_config_address), "w") as file:
        json.dump(config, file, indent=4)

def is_container_running(docker_client, image_name):
    for container in docker_client.containers.list():
        if image_name in container.image.tags:
            return container
    return None

def run_program(arguments):
    with open(arguments.config) as file:
        settings = json.load(file)

    check_vehicles_uniqueness(settings["vehicles"])

    client = docker.from_env()
    port = settings["start-port"]
    logging.info("Starting docker containers")

    start_mqtt_broker_container(client, settings)

    os.makedirs(os.path.abspath(TMP_CONFIG_PATH), mode=0o777, exist_ok=True)
    for vehicle in settings["vehicles"]:
        while not is_port_available(port):
            logging.info(f"Port {port} is not available, trying next one")
            port += 1

        if not (1024 < port < 65535):
            raise PortOutOfRangeException()

        logging.info(f"Starting vehicle {vehicle['name']} with port {port}")
        start_containers(client, settings, vehicle, port)
        port += 1

    logging.info("Start was successful")
    end = False
    while not end:
        for container in runningContainers:
            container.reload()
            if not container.attrs["State"]["Running"]:
                logging.info(
                    f"Error, container with id {container.short_id} is not running, app will exit, container output: \n ==== \n{container.logs(tail=LOG_LAST_LINES).decode()} \n ==== \n"
                )
                end = True
        if not end:  # don't want to wait 5 seconds, if containers crash in the beginning
            time.sleep(5)
    stop_containers()
    remove_tmp_config_files()

def start_mqtt_broker_container(docker_client, settings):
    image_tag = f"{settings['vernemq-docker-image']}:{settings['vernemq-docker-tag']}"
    mqtt_broker_container = is_container_running(docker_client, image_tag)
    if mqtt_broker_container:
        logging.info(f"Using existing mqtt broker docker container with id {mqtt_broker_container.short_id}")
        return

    mqtt_broker_container = docker_client.containers.run(
        image_tag,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(VERNEMQ_CONFIG): {"bind": "/vernemq/etc", "mode": "ro"},
            os.path.abspath("."): {"bind": "/vernemq/log", "mode": "rw"},
        },
    )

    logging.info(f"Started a mqtt broker docker container with id {mqtt_broker_container.short_id}")
    runningContainers.append(mqtt_broker_container)

def start_containers(docker_client, settings, vehicle, port):
    create_config_files(vehicle)
    vehicle_id = f"{vehicle['company']}-{vehicle['name']}"

    external_server_image_tag = f"{settings['external-server-docker-image']}:{settings['external-server-docker-tag']}"
    external_server_container = docker_client.containers.run(
        external_server_image_tag,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(TMP_CONFIG_PATH): {"bind": "/home/bringauto/config", "mode": "ro"},
            os.path.abspath("."): {"bind": "/home/bringauto/log", "mode": "rw"},
        },
        entrypoint=[
            "python3",
            "/home/bringauto/external_server/external_server_main.py",
            f"--config=/home/bringauto/config/{vehicle_id}-external-server-config.json",
        ],
    )
    logging.info(f"Started an external server docker container with id {external_server_container.short_id}")
    runningContainers.append(external_server_container)

    gateway_image_tag = f"{settings['gateway-docker-image']}:{settings['gateway-docker-tag']}"
    gateway_container = docker_client.containers.run(
        gateway_image_tag,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(TMP_CONFIG_PATH): {"bind": "/home/bringauto/config", "mode": "ro"},
            os.path.abspath("."): {"bind": "/gateway/log", "mode": "rw"},
        },
        entrypoint=[
            "/home/bringauto/module-gateway/bin/module-gateway-app",
            f"--config-path=/home/bringauto/config/{vehicle_id}-gateway-config.json",
            f"--port={port}",
            "--verbose",
        ],
    )
    logging.info(f"Started a gateway docker container with id {gateway_container.short_id}")
    runningContainers.append(gateway_container)

    vehicle_image_tag = f"{settings['vehicle-docker-image']}:{settings['vehicle-docker-tag']}"
    vehicle_container = docker_client.containers.run(
        vehicle_image_tag,
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath(os.path.join(CONFIG_PATH, "virtual-vehicle")): {"bind": "/virtual-vehicle-utility/config", "mode": "ro"},
            os.path.abspath("."): {"bind": "/virtual-vehicle-utility/log", "mode": "rw"},
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
    runningContainers.append(vehicle_container)

def exit_gracefully(signum, frame):
    signal.signal(signal.SIGINT, original_sigint)
    logging.info("Signal received, quitting")
    try:
        stop_containers()
        remove_tmp_config_files()
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(1)

def stop_and_remove_container(container, log_dir):
    logging.info(
        f"Stopping container with id {container.short_id} [{runningContainers.index(container) + 1}/{len(runningContainers)}]"
    )
    try:
        log_filename = f"{container.short_id}.log"
        with open(log_dir / log_filename, "w") as text_file:
            text_file.write(container.logs().decode())
        container.stop()
        container.remove()
    except docker.errors.NotFound:
        logging.error(f"Container {container.short_id} was not found")
    except docker.errors.APIError as e:
        logging.error(f"API error while stopping container {container.short_id}: {str(e)}")

def stop_containers():
    log_dir = pathlib.Path(LOGS_DIR)
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(stop_and_remove_container, container, log_dir) for container in runningContainers]
        for future in futures:
            future.result()  # Wait for all futures to complete

def remove_tmp_config_files():
    try:
        shutil.rmtree(TMP_CONFIG_PATH)
    except OSError as e:
        logging.error(f"Error while removing tmp-configs directory: {str(e)}")

if __name__ == "__main__":
    logging.basicConfig(
        format="[%(asctime)s] [%(levelname)s] %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S"
    )
    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, exit_gracefully)
    signal.signal(signal.SIGTERM, exit_gracefully)

    args = parse_arguments()
    try:
        check_paths(args)
        run_program(args)
    except Exception as e:
        logging.error(str(e))
        exit_gracefully(None, None)
