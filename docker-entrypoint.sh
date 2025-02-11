#!/bin/sh

set -e

if [ -n "$ENV_PATH" ] && [ -f "$ENV_PATH" ]; then
       source  "$ENV_PATH"
fi

#run no more than 12h by default
TIMEOUT=${TIMEOUT:-43200}

if [[ "$@" == "run" ]]; then
        echo "Checking mandatory environment variables"
	flag=""
	if [ -z "$WIKI_SERVER" ] || [ -z "$WIKI_APIKEY" ] || [ -z "$WIKI_PROJECT" ] || [ -z "$WIKI_STACKS_PAGE" ] || [ -z "$WIKI_HOSTS_PAGE" ] || [ -z "$WIKI_CONTAINERS_PAGE" ]; then
		echo "Did not receive all Taskman related variables, will run in info mode"
		flag="-n"
	fi

	if [[ "$DEBUG" == "Yes" ]]; then
		flag="${flag} -v"
	fi

	python /wait_for_redmine.py

  if [ -n "$WIKI_SERVER" ] && [ -n "$WIKI_APIKEY" ] && [ -n "$WIKI_PROJECT" ] && [ -n "$WIKI_PAGE" ] && [ -n "$RANCHER_CONFIG" ]; then
    echo "Received RANCHER_CONFIG and WIKI related variables"

    flag="${flag} -p ${WIKI_PAGE}"
  fi

else
  exec  "$@"
fi

