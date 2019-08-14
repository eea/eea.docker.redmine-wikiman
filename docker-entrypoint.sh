#!/bin/sh

set -e

if [[ "$@" == "run" ]]; then
        echo "Checking mandatory environment variables"
	if [ -z "$RANCHER_CONFIG" ]; then
		echo "Did not receive rancher configuration!!! Exiting!"
		exit 1
	fi
	flag=""
	if [ -z "$WIKI_SERVER" ] || [ -z "$WIKI_APIKEY" ] || [ -z "$WIKI_PROJECT" ] || [ -z "$WIKI_STACKS_PAGE" ] || [ -z "$WIKI_HOSTS_PAGE" ] || [ -z "$WIKI_CONTAINERS_PAGE" ]; then
		echo "Did not receive all Taskman related variables, will run in info mode"
		flag="-n"
	fi

	if [ -z "$DEBUG" ]; then
		flag="${flag} -v"
	fi

	echo "Running listhosts.py"
	python /listhosts.py $flag

        echo "Running liststacks.py"
        python /liststacks.py $flag

	echo "Running listcontainers.py"
        python /listcontainers.py $flag
else
	exec  "$@"
fi

