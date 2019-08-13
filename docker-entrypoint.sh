#!/bin/sh

set -e

if [[ "$@" == "run" ]]; then
        echo "Checking mandatory environment variables"
	if [ -z "$rancher_config" ]; then
		echo "Did not receive rancher configuration!!! Exiting!"
		exit 1
	fi
	flag=""
	if [ -z "$wiki_server" ] || [ -z "$wiki_apikey" ] || [ -z "$wiki_project" ] || [ -z "$wiki_stacks_page" ] || [ -z "$wiki_hosts_page" ] || [ -z "$wiki_containers_page" ]; then
		echo "Did not receive all Taskman related variables, will run in info mode"
		flag="-n"
	fi

	if [ -z "$debug" ]; then
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

