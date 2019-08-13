import json, time, shelve
import urllib.request, urllib.parse, urllib.error, urllib.request, urllib.error, urllib.parse, os, sys, logging
import getopt

from redminelib import Redmine
from urllib.parse import urlparse


class RancherInstances(object):

    content = []

    def __init__(self, servers):
        self.totalAvailable = 0
        pageTitle = os.getenv('wiki_hostspagetitle', 'Rancher Hosts')

        self.open_shelf()
        self.content = []
        self.content.append('{{>toc}}\n\n')
        self.content.append('h1. ' + pageTitle + '\n')
        self.content.append('Automatically discovered on ' + time.strftime('%d %B %Y') + '. _Do not update this page manually._')

        rancher_configs = os.getenv('rancher_config')
        for rancher_config in rancher_configs.split():
            rancher_configuration = rancher_config.split(",")
            rancherUrl = rancher_configuration[0]
            rancherApiUrl = rancherUrl + "/v2-beta"
            rancherAccessKey = rancher_configuration[1]
            rancherSecretKey = rancher_configuration[2]
            logging.info("Starting " + rancherUrl)
            
            self.content.append('\nh2. {}\n'.format(urlparse(rancherUrl).netloc.upper()))

            envstruct = self.get_operation(rancherApiUrl, rancherAccessKey, rancherSecretKey,  rancherApiUrl + "/projects")
            for project in sorted(envstruct['data'], key=getKey):
                if project['state'] != 'active': continue
                environment = project['id']
                stackUrl =   rancherApiUrl + "/projects/" + environment
                logging.info("Retrieving %s - %s", environment, project['name'])
                
                envURL = rancherUrl + "/env/" + environment
                envLabel = project['name']

                self.content.append('\nh3. "{}":{}\n'.format(envLabel, envURL))


                self.content.append('|_{width:14em}. Name |_{width:6em}. Total RAM |_{width:5em}. Available |_{width:9em}. IP |_. Docker |_. OS |')
                try:
                    
                    structdata = self.get_operation(rancherApiUrl, rancherAccessKey, rancherSecretKey, stackUrl + "/hosts")
                    self.totalAvailable = 0
                    for instance in sorted(structdata['data'], key=getHostKey):
                        self.add_instance(instance)
                        self.shelve_instance(instance, envLabel)
                except:
                    logging.error("Unable to get hosts for %s", environment)
                self.content.append('\nAvailable RAM in environment: {:.1f} GB'.format(self.totalAvailable / 1024.0))
        self.close_shelf()

    def open_shelf(self):
        shelfFile = os.getenv('shelve_file','/tmp/shelve')
        self.shelfFD = shelve.open(shelfFile, flag='n')

    def close_shelf(self):
        self.shelfFD.close()

    def shelve_instance(self, instance, environment):
        ips = self.getIPnumbers(instance['publicEndpoints'])
        for ip in ips:
            self.shelfFD[str(ip)] = environment

    def add_instance(self, instance):
        name = instance['hostname']
        state = instance['state']
        created = instance['created'][:10]
        ips = self.getIPnumbers(instance['publicEndpoints'])
        infoStruct = instance['info']
        osInfo = infoStruct['osInfo']
        dockerVersion = osInfo['dockerVersion']
        operatingSystem = osInfo['operatingSystem']
        memoryInfo = infoStruct['memoryInfo']
        memTotal = memoryInfo['memTotal']
        memAvailable = memoryInfo.get('memAvailable', 0)
        self.totalAvailable = self.totalAvailable + int(memAvailable)

        self.content.append('|{} |>. {} |>. {} | {} | {} | {} |'.format(name, int(memTotal + 0.5), memAvailable, ", ".join(ips), dockerVersion, operatingSystem))

    def write_page(self):
        server = os.getenv('wiki_server','')
        apikey = os.getenv('wiki_apikey','')
        projectName = os.getenv('wiki_project','')
        pageName = os.getenv('wiki_hosts_page','')
        server = Redmine(server, key=apikey, requests={'verify': True})
        server.wiki_page.update(pageName, project_id=projectName, text="\n".join(self.content))

    def write_stdout(self):
        for line in self.content:
            print(line)

    def get_operation(self, rancherUrl, rancherAccessKey, rancherSecretKey, url):
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib.request.HTTPBasicAuthHandler()
        auth_handler.add_password(realm=realm, uri=rancherUrl, user=rancherAccessKey, passwd=rancherSecretKey)
        opener = urllib.request.build_opener(auth_handler)
        urllib.request.install_opener(opener)
        logging.debug("Opening: %s", url)
        f = urllib.request.urlopen(url)
        rawdata = f.read()
        f.close()
        return json.loads(rawdata)

    def getIPnumbers(self, netList):
        ipDict = {}
        for net in netList:
            ipDict[net['ipAddress']] = 1
        return list(ipDict.keys())

def addToShelf(self, instance):
    pass

def getKey(instance):
    """ Return the key to sort on """
    return instance['name']

def getHostKey(instance):
    """ Return the key to sort on """
    return instance['hostname']


if __name__ == '__main__':
    dryrun = False
    environments = None

    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
    try:
        opts, args = getopt.getopt(sys.argv[1:], "dvn")
    except getopt.GetoptError as err:
        sys.exit(2)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
        if o == "-n":
            dryrun = True
    if len(args) > 0:
        environments = args
    obj = RancherInstances(environments)
    if dryrun == True:
        obj.write_stdout()
    else:
        obj.write_page()
    logging.info("Done")
