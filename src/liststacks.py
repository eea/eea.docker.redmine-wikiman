import json
import urllib, urllib2, os, sys, logging, time, getopt
from redminelib import Redmine


class Discover(object):


    def write_page(self, content):
        server = os.getenv('wiki_server','')
        apikey = os.getenv('wiki_apikey','')
        projectName = os.getenv('wiki_project','')
        pageName = os.getenv('wiki_stacks_page','')
        server = Redmine(server, key=apikey, requests={'verify': True})
        server.wiki_page.update(pageName, project_id=projectName, text=content)

    def write_stdout(self, content):
        #pass
        print content

    def get_operation(self, rancherUrl, rancherAccessKey, rancherSecretKey, url):
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib2.HTTPBasicAuthHandler()
        auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
        opener = urllib2.build_opener(auth_handler)
        urllib2.install_opener(opener)
        f = urllib2.urlopen(url)
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
    except getopt.GetoptError, err:
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
        if o == "-n":
            dryrun = True


    pageTitle = os.getenv('wiki_stackspagetitle', 'Rancher Stacks')

    content = []
    content.append('{{>toc}}\n\n')
    content.append('h1. ' + pageTitle + '\n\n')
    content.append('Automatically discovered on ' + time.strftime('%d %B %Y') + '. _Do not update this page manually._')

    disc = Discover()
    rancher_configs = os.getenv('rancher_config')
    logging.debug("%s", rancher_configs)
    for rancher_config in rancher_configs.split():
        logging.debug("%s", rancher_config)
        rancher_configuration = rancher_config.split(",")
        logging.debug("%s", rancher_configuration)
        rancherUrl = rancher_configuration[0]
        rancherApiUrl = rancherUrl + "/v2-beta"
        rancherAccessKey = rancher_configuration[1]
        rancherSecretKey = rancher_configuration[2]
        
        structdata = disc.get_operation( rancherApiUrl, rancherAccessKey, rancherSecretKey, rancherApiUrl+"/projects")
        for project in sorted(structdata['data'], key=getKey):
            if project['state'] != 'active': continue
            environment = project['id']
            envURL = rancherUrl + "/env/" + environment
            logging.debug("Retrieving %s - %s", environment, project['name'])
            content.append('\nh2. "{}":{}\n'.format(project['name'], envURL))
            description = project.get('description')
            if description is None: description = u''
            content.append('{}\n'.format(description.encode("utf8")))
            content.append('|_. Name |_. Created |_. Description |_. Tags |')

            stackUrl =   rancherApiUrl + "/projects/" + environment 
            structdata = disc.get_operation(rancherApiUrl, rancherAccessKey, rancherSecretKey, stackUrl + "/stacks")
#           print structdata
#           sys.exit()

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
                content.append('|"{}":{} | {} | {} | {} |'.format(name, link, created, description.encode("utf8"), group))

    if dryrun:
        disc.write_stdout("\n".join(content))
    else:
        disc.write_page("\n".join(content))
