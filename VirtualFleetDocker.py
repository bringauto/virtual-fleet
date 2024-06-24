import argparse
import json
import signal
import sys
import time
import docker
import logging
import os
import shutil
import pathlib
import socket

LOG_LAST_LINES = 5
runningContainers = []


class FileDoesntExistException(Exception):
    def __init__(self, message) -> None:
        super().__init__(message)


class PortOutOfRangeException(Exception):
    def __init__(self, message="Ports out of range, it has to be: 1024 < my port < 65535") -> None:
        super().__init__(message)


def parse_arguments():
    parser = argparse.ArgumentParser()
    required_named = parser.add_argument_group("required arguments")
    required_named.add_argument("--json_path", required=True, help="path to json file")
    return parser.parse_args()


def check_paths(arguments):
    if not os.path.exists(arguments.json_path):
        raise FileDoesntExistException("Json file " + arguments.json_path + " doesnt exist")

def is_port_avialable(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0

def check_vehicles_uniqueness(vehicles):
    vehicle_names = []
    for vehicle in vehicles:
        vehicle_id = vehicle["company"] + "-" + vehicle["name"]
        if vehicle_id in vehicle_names:
            raise ValueError(f"Vehicle {vehicle_id} is not unique")
        vehicle_names.append(vehicle_id)


def create_config_files(vehicle):
    config_address = "./config/external-server/config.json"
    with open(os.path.abspath(config_address), "r") as file:
        config = json.load(file)
    config["company_name"] = vehicle["company"]
    config["car_name"] = vehicle["name"]
    tmp_config_address = f"./config/tmp-configs/{vehicle['company']}-{vehicle['name']}-external-server-config.json"
    with open(os.path.abspath(tmp_config_address), "w") as file:
        json.dump(config, file, indent=4)

    config_address = "./config/module-gateway/config.json"
    with open(os.path.abspath(config_address), "r") as file:
        config = json.load(file)
    config["external-connection"]["company"] = vehicle["company"]
    config["external-connection"]["vehicle-name"] = vehicle["name"]
    tmp_config_address = f"./config/tmp-configs/{vehicle['company']}-{vehicle['name']}-gateway-config.json"
    with open(os.path.abspath(tmp_config_address), "w") as file:
        json.dump(config, file, indent=4)


def run_program(arguments):
    file = open(arguments.json_path)
    settings = json.load(file)
    file.close()

    check_vehicles_uniqueness(settings["vehicles"])

    client = docker.from_env()
    port = settings["start-port"]
    logging.info("Starting docker containers")

    start_mqtt_broker(client, settings)
    
    os.makedirs(os.path.abspath("./config/tmp-configs"), mode=0o777, exist_ok=True)
    for vehicle in settings["vehicles"]:
        while not is_port_avialable(port):
            logging.info(f"Port {port} is not available, trying next one")
            port += 1

        if (port <= 1024) or (port > 65535):
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

    try:
        shutil.rmtree(os.path.abspath("./config/tmp-configs"))
    except OSError as e:
        logging.error("Error while removing tmp-configs directory: " + str(e))


def start_mqtt_broker(docker_client, settings):
    mqtt_broker_container = docker_client.containers.run(
        settings["vernemq-docker-image"] + ":" + settings["vernemq-docker-tag"],
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath("./config/vernemq"): {"bind": "/vernemq/etc", "mode": "ro"},
            os.path.abspath("."): {"bind": "/vernemq/log", "mode": "rw"},
        },
    )
    
    logging.info("Started a mqtt broker docker container with id " + mqtt_broker_container.short_id)
    runningContainers.append(mqtt_broker_container)


def start_containers(docker_client, settings, vehicle, port):
    create_config_files(vehicle)
    vehicle_id = vehicle["company"] + "-" + vehicle["name"]

    external_server_container = docker_client.containers.run(
        settings["external-server-docker-image"] + ":" + settings["external-server-docker-tag"],
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath("./config/tmp-configs/"): {"bind": "/home/bringauto/config", "mode": "ro"},
            os.path.abspath("."): {"bind": "/home/bringauto/log", "mode": "rw"},
        },
        entrypoint=[
            "python3",
            "/home/bringauto/external_server/external_server_main.py",
            f"--config=/home/bringauto/config/{vehicle_id}-external-server-config.json",
        ],
    )
    logging.info("Started a external server docker container with id " + external_server_container.short_id)
    runningContainers.append(external_server_container)

    gateway_container = docker_client.containers.run(
        settings["gateway-docker-image"] + ":" + settings["gateway-docker-tag"],
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath("./config/tmp-configs/"): {"bind": "/home/bringauto/config", "mode": "ro"},
            os.path.abspath("."): {"bind": "/gateway/log", "mode": "rw"},
        },
        entrypoint=[
            "/home/bringauto/module-gateway/bin/module-gateway-app",
            f"--config-path=/home/bringauto/config/{vehicle_id}-gateway-config.json",
            "--port=" + str(port),
            "--verbose",
        ],
    )
    logging.info("Started a gateway docker container with id " + gateway_container.short_id)
    runningContainers.append(gateway_container)

    vehicle_container = docker_client.containers.run(
        settings["vehicle-docker-image"] + ":" + settings["vehicle-docker-tag"],
        detach=True,
        auto_remove=False,
        network_mode="host",
        volumes={
            os.path.abspath("./config/virtual-vehicle"): {"bind": "/virtual-vehicle-utility/config", "mode": "ro"},
            os.path.abspath("."): {"bind": "/virtual-vehicle-utility/log", "mode": "rw"},
        },
        entrypoint=[
            "/virtual-vehicle-utility/bin/virtual-vehicle-utility",
            "--config=/virtual-vehicle-utility/config/config.json",
            "--verbose",
            "--module-gateway-ip=" + settings["module-gateway-ip"],
            "--module-gateway-port=" + str(port),
        ],
    )

    logging.info("Started a vehicle docker container with id " + vehicle_container.short_id)
    runningContainers.append(vehicle_container)


def exit_gracefully(signum, frame):
    signal.signal(signal.SIGINT, original_sigint)
    logging.info("Signal received, quitting")
    try:
        stop_containers()
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(1)


def stop_containers():
    log_dir = pathlib.Path("logs/")
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir()
    for index, container in enumerate(runningContainers):
        logging.info(
            "Stopping container with id "
            + container.short_id
            + " ["
            + str(index + 1)
            + "/"
            + str(len(runningContainers))
            + "]"
        )
        try:
            log_filename = f"{container.short_id}.log"
            with open(log_dir / log_filename, "w") as text_file:
                text_file.write(f"{container.logs().decode()}")
            container.stop()
            container.remove()
        except docker.errors.NotFound:
            logging.error("Container " + container.short_id + " was not found")
        except docker.errors.APIError as e:
            logging.error("API error while stopping container " + container.short_id + ": " + str(e))


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
        print(f"Main exception: {str(e)}")
        exit_gracefully(None, None)
