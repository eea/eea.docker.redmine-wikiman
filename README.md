# Redmine wiki manager
Improves redmine wikis adding Rancher, GitHub and DockerHub links.
The image is created under the name: eeacms/redmine-wikiman


## Variables

1. RANCHER_CONFIG - Rancher configuration, Multiline, with the format - rancher url, access key, secret key ( separated by commas)
2. WIKI_SERVER - Redmine url - The main url of the redmine instance.
3. WIKI_APIKEY - Redmine API key - Belongs to the user that will update the wiki pages.
4. WIKI_PROJECT - Redmine wiki project, the redmine project where the wiki pages are.
5. WIKI_HOSTS_PAGE - Redmine wiki hosts page
6. WIKI_STACKS_PAGE - Redmine wiki stacks page
7. WIKI_CONTAINERS_PAGE - Redmine wiki containers page
8. WIKI_PAGE - Redmine wiki with applications
9. SVN_USER - SVN user to read docker-compose.yml
10. SVN_PASSWORD - SVN password to read docker-compose.yml
11. DEBUG - Set to "Yes" to enable debug logging

## Usage

### Will generate the wiki pages in stdout, no redmine necessary

     docker run --rm -e RANCHER_CONFIG="RANCHER-URL,RANCHER-ACCESS-KEY,RANCHER-SECRET-KEY" eeacms/redmine-wikiman

### Will save the wiki pages in redmine

     docker run --rm -e RANCHER_CONFIG="RANCHER-URL,RANCHER-ACCESS-KEY,RANCHER-SECRET-KEY RANCHER2-URL,RANCHER2-ACCESS-KEY,RANCHER2-SECRET-KEY" -e WIKI_SERVER="REDMINE_URL" -e WIKI_APIKEY="REDMINE-KEY" -e WIKI_PROJECT=project -e WIKI_HOSTS_PAGE=RancherHosts -e WIKI_STACKS_PAGE=RancherStacks -e WIKI_CONTAINERS_PAGE=RancherContainers eeacms/redmine-wikiman

## Scripts

The scripts can be run in python, with the following arguments:
* -v - debug logging
* -n - wiki is not uploaded to redmine

For example:
    python /listcontainers.py -v -n


### listcontainers.py

Updates a redmine wiki page with the list of current docker containers per rancher environment ( including image, stack, status), calculating memory reservation and limit per host/environment/rancher and comparing it with the real memory values.

### listhosts.py

Updates a redmine wiki page with the list of current hosts  per rancher environment ( including check_mk link, docker version, OS version), calculating available and used memory percentages.


### liststacks.py

Updates a redmine wiki page with the list of current rancher stacks per environment ( including date created, state and health, catalog, description and tags ).

### addimageinfo.py

Updates all wiki subpages for a redmine wiki page, searching for "DeploymentRepoURL" url and extracting docker image information ( docker hub link and github link ) and adding and populating "Source code information" section of the wiki with it.

### addstackinfo.py

Updates all wiki subpages for a redmine wiki page, searching for "Service location" link and extracting it from the current rancher stacks list (created by liststacks.py) and adding the stack link in the field "Rancher Stack URL" to it.
    
## Development

To make the development easier, you can mount the scripts in a volume and run them:

    docker run -v $(pwd)/src:/src -e RANCHER_CONFIG="RANCHER-URL,RANCHER-ACCESS-KEY,RANCHER-SECRET-KEY RANCHER2-URL,RANCHER2-ACCESS-KEY,RANCHER2-SECRET-KEY" -e WIKI_SERVER="REDMINE_URL" -e WIKI_APIKEY="REDMINE-KEY" -e WIKI_PROJECT=project -e WIKI_HOSTS_PAGE=RancherHosts -e WIKI_STACKS_PAGE=RancherStacks -e WIKI_CONTAINERS_PAGE=RancherContainers eeacms/rancher2redmine sh
    $python /src/listcontainers.py -v

