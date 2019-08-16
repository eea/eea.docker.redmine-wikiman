import json
import urllib.request, urllib.parse, urllib.error, urllib.request, urllib.error, urllib.parse, os, sys, logging, time, getopt
from redminelib import Redmine
from urllib.parse import urlparse

class Discover(object):


    def write_page(self, content):
        server = os.getenv('WIKI_SERVER','')
        apikey = os.getenv('WIKI_APIKEY','')
        projectName = os.getenv('WIKI_PROJECT','')
        pageName = os.getenv('WIKI_STACKS_PAGE','')
        server = Redmine(server, key=apikey, requests={'verify': True})
        server.wiki_page.update(pageName, project_id=projectName, text=content)

    def write_stdout(self, content):
        #pass
        print(content)

    def get_operation(self, rancherUrl, rancherAccessKey, rancherSecretKey, url):
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib.request.HTTPBasicAuthHandler()
        auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
        opener = urllib.request.build_opener(auth_handler)
        urllib.request.install_opener(opener)
        f = urllib.request.urlopen(url)
        rawdata = f.read()
        f.close()
        return json.loads(rawdata)

def getKey(instance):
    """ Return the key to sort on """
    return instance['name']

if __name__ == '__main__':
    dryrun = False


    try:
        opts, args = getopt.getopt(sys.argv[1:], "vn")
    except getopt.GetoptError as err:
        sys.exit(2)
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
        if o == "-n":
            dryrun = True


    pageTitle = os.getenv('WIKI_STACKSPAGETITLE', 'Rancher Stacks')

    content = []
    content.append('{{>toc}}\n\n')
    content.append('h1. ' + pageTitle + '\n\n')
    content.append('Automatically discovered on ' + time.strftime('%d %B %Y') + '. _Do not update this page manually._')

    disc = Discover()
    rancher_configs = os.getenv('RANCHER_CONFIG')
    for rancher_config in rancher_configs.split():
        rancher_configuration = rancher_config.split(",")
        rancherUrl = rancher_configuration[0]
        rancherApiUrl = rancherUrl + "/v2-beta"
        rancherAccessKey = rancher_configuration[1]
        rancherSecretKey = rancher_configuration[2]
        logging.info(rancherUrl)
      
        content.append('\nh2. {}\n'.format(urlparse(rancherUrl).netloc.upper()))


        structdata = disc.get_operation( rancherApiUrl, rancherAccessKey, rancherSecretKey, rancherApiUrl+"/projects")
        for project in sorted(structdata['data'], key=getKey):
            if project['state'] != 'active': continue
            environment = project['id']
            envURL = rancherUrl + "/env/" + environment
            logging.info("Retrieving %s - %s", environment, project['name'])
            content.append('\nh3. "{}":{}\n'.format(project['name'], envURL))
            description = project.get('description')
            if description is None: description = ''
            content.append('{}\n'.format(description))
            content.append('|_. Name |_. Created |_. State |_. Health |_. Catalog |_. Description |_. Tags |')
            infraStacks = []
            userStacks = []

            stackUrl =   rancherApiUrl + "/projects/" + environment 
            structdata = disc.get_operation(rancherApiUrl, rancherAccessKey, rancherSecretKey, stackUrl + "/stacks")

            for instance in sorted(structdata['data'], key=getKey):
                actions = instance['actions']
                name = instance['name']
                link = envURL + "/apps/stacks/" + instance['id']
                created = instance['created'][:10]
                system = instance.get('system', False)
                if system is True: name = ">. _" + name + "_"
                description = instance.get('description', '')
                if description is None: description = ''
                if description.find('\n') >= 0: description = description[:description.find('\n')]
                group = instance.get('group', '')
                if group is None: group = ''
                logging.debug("%s", name)
                stack_line = '|"{}":{} | {} | {} | {} | {} | {} | {} |'.format(name, link, created, instance['state'], instance['healthState'], instance['externalId'], description, group)
                if (instance['system']):
                    infraStacks.append(stack_line)
                else:
                    userStacks.append(stack_line)
            content.extend(infraStacks)
            content.extend(userStacks)

    if dryrun:
        disc.write_stdout("\n".join(content))
    else:
        disc.write_page("\n".join(content))
