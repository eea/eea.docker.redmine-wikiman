#!/bin/sh

set -e

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
      
	if [ -n "$RANCHER_CONFIG" ]; then

		echo "Received RANCHER_CONFIG value, will run rancher related scripts"

		echo "Running listhosts.py"
		python /listhosts.py $flag

        	echo "Running liststacks.py"
        	python /liststacks.py $flag

		echo "Running listcontainers.py"
        	python /listcontainers.py $flag

	fi

	if [ -n "$WIKI_SERVER" ] && [ -n "$WIKI_APIKEY" ] && [ -n "$WIKI_PROJECT" ] && [ -n "$WIKI_PAGE" ]; then
                
		echo "Received redmine related information, will run wiki enrichment scripts"

		echo "Running addstackinfo.py"
		python /addstackinfo.py $flag

		echo "Running addimageinfo.py"
        	python /addimageinfo.py $flag
        fi

		echo "Running applytemplate.py"
		python /applytemplate.py $flag $WIKI_PAGE

else
	exec  "$@"
fi

