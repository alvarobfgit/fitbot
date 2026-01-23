#!/bin/sh

# ALWAYS OPEN FROM THE TEXT EDITOR IN THE NAS. OTHERWISE PROBLEMS WITH /r IF EDITED IN WINDOWS
# This script MUST be executed as ROOT user

PATH="/volume1/sourcecode/GitHub/latest/fitbot-master"
URL_DOCKER_FILE="/volume1/sourcecode/GitHub/latest/fitbot-master/DockerfilePrecompiled"

cd ${PATH}
var=$(pwd)
echo "The current working directory is: $var."

/usr/local/bin/docker build -t fitbot-alvaro-precompiled -f ${URL_DOCKER_FILE} .

echo "Build finished"
