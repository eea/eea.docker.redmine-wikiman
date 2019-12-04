import json
import time
import shelve
import urllib.request
import urllib.parse
import urllib.error
import urllib.request
import urllib.error
import urllib.parse
import os
import sys
import logging
import getopt

from redminelib import Redmine
from urllib.parse import urlparse


class RancherInstances(object):

    content = []

    def __init__(self, servers):
        self.totalAvailable = 0
        self.total = 0
        self.fullAvailable = 0
        self.fullTotal = 0
        self.allAvailable = 0
        self.allTotal = 0
        self.envText = []
        self.fullText = []
        pageTitle = os.getenv('WIKI_HOSTSPAGETITLE', 'Rancher Hosts')

        self.open_shelf()
        self.content = []
        self.content.append('{{>toc}}\n\n')
        self.content.append('h1. ' + pageTitle + '\n')
        self.content.append(
            'Automatically discovered on ' +
            time.strftime('%d %B %Y') +
            '. _Do not update this page manually._')

        rancher_configs = os.getenv('RANCHER_CONFIG')
        for rancher_config in rancher_configs.split():
            rancher_configuration = rancher_config.split(",")
            rancherUrl = rancher_configuration[0]
            rancherApiUrl = rancherUrl + "/v2-beta"
            rancherAccessKey = rancher_configuration[1]
            rancherSecretKey = rancher_configuration[2]
            logging.info("Starting " + rancherUrl)
            self.fullAvailable = 0
            self.fullTotal = 0
            self.envText = []
            self.fullText.append(
                '\nh2. {}\n'.format(
                    urlparse(rancherUrl).netloc.upper()))
            try:
                envstruct = self.get_operation(
                    rancherApiUrl,
                    rancherAccessKey,
                    rancherSecretKey,
                    rancherApiUrl + "/projects")
            except BaseException:
                logging.error("There was a problem reading from Rancher")
                logging.error(sys.exc_info())
                continue

            for project in sorted(envstruct['data'], key=getKey):
                if project['state'] != 'active':
                    continue
                environment = project['id']
                stackUrl = rancherApiUrl + "/projects/" + environment
                logging.info(
                    "Retrieving %s - %s",
                    environment,
                    project['name'])

                envURL = rancherUrl + "/env/" + environment
                envLabel = project['name']

                self.envText.append('\nh3. "{}":{}\n'.format(envLabel, envURL))
                description = project.get('description')
                if description is None:
                    description = ''
                self.envText.append('{}\n'.format(description))

                self.envText.append(
                    '|_{width:14em}. Name |_. Check_MK |_. Total RAM |_. Used |_. %Used |_. Available |_{width:9em}. IP |_. Docker |_. OS |')
                try:
                    structdata = self.get_operation(
                        rancherApiUrl, rancherAccessKey, rancherSecretKey, stackUrl + "/hosts")
                    self.totalAvailable = 0
                    self.total = 0
                    for instance in sorted(structdata['data'], key=getHostKey):
                        instance['host_url'] = rancherUrl + "/env/" + \
                            environment + "/infra/hosts/" + instance['id']
                        instance['check_mk'] = "https://goldeneye.eea.europa.eu/omdeea/check_mk/index.py?start_url=%2Fomdeea%2Fcheck_mk%2Fview.py%3Fview_name%3Dhost%26host%3D" + \
                            instance['hostname'].split(
                                '.')[0] + "%26site%3Domdeea"
                        self.add_instance(instance)
                        self.shelve_instance(instance, envLabel)
                except BaseException:
                    logging.error("Unable to get hosts for %s", environment)
                self.envText.append(
                    '\nAvailable RAM in environment: {:.1f} GiB from a total of {:.1f} GiB'.format(
                        self.totalAvailable / 1024.0, self.total / 1024))
                self.envText.append(
                    '\nUsed RAM {}'.format(
                        self.color_percent(
                            (self.total - self.totalAvailable) * 100 / self.total)))
                self.fullAvailable = self.fullAvailable + self.totalAvailable
                self.fullTotal = self.fullTotal + self.total
            self.fullText.append(
                '\nAvailable RAM: {:.1f} GiB from a total of {:.1f} GiB'.format(
                    self.fullAvailable / 1024.0, self.fullTotal / 1024))
            self.fullText.append(
                '\nUsed RAM {}'.format(
                    self.color_percent(
                        (self.fullTotal -
                         self.fullAvailable) *
                        100 /
                        self.fullTotal)))
            self.fullText.extend(self.envText)
            self.allAvailable = self.allAvailable + self.fullAvailable
            self.allTotal = self.allTotal + self.fullTotal
        self.content.append(
            '\nAvailable RAM: {:.1f} GiB from a total of {:.1f} GiB'.format(
                self.allAvailable / 1024.0,
                self.allTotal / 1024))
        self.content.append(
            '\nUsed RAM {}'.format(
                self.color_percent(
                    (self.allTotal -
                     self.allAvailable) *
                    100 /
                    self.allTotal)))
        self.content.extend(self.fullText)

        self.close_shelf()

    def color_percent(self, number):
        text = '{:.1f}&#37;'.format(number)
        if number > 80:
            text = '%{background:pink}' + text + '%'
        return text

    def open_shelf(self):
        shelfFile = os.getenv('SHELVE_FILE', '/tmp/shelve')
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
        memUsed = memTotal - memAvailable
        memUsedPercentage = memUsed * 100 / memTotal
        self.totalAvailable = self.totalAvailable + int(memAvailable)
        self.total = self.total + int(memTotal)
        self.envText.append(
            '| "{}":{} | "{}":{} |>. {} |>. {} |>. {} |>. {} | {} | {} | {} |'.format(
                name,
                instance['host_url'],
                name.split('.')[0],
                instance['check_mk'],
                memTotal,
                memUsed,
                self.color_percent(memUsedPercentage),
                memAvailable,
                ", ".join(ips),
                dockerVersion,
                operatingSystem))

    def write_page(self):
        server = os.getenv('WIKI_SERVER', '')
        apikey = os.getenv('WIKI_APIKEY', '')
        projectName = os.getenv('WIKI_PROJECT', '')
        pageName = os.getenv('WIKI_HOSTS_PAGE', '')
        server = Redmine(server, key=apikey, requests={'verify': True})
        server.wiki_page.update(
            pageName,
            project_id=projectName,
            text="\n".join(
                self.content))

    def write_stdout(self):
        for line in self.content:
            print(line)

    def get_operation(self, rancherUrl, rancherAccessKey,
                      rancherSecretKey, url):
        realm = "Enter API access key and secret key as username and password"

        auth_handler = urllib.request.HTTPBasicAuthHandler()
        auth_handler.add_password(
            realm=realm,
            uri=rancherUrl,
            user=rancherAccessKey,
            passwd=rancherSecretKey)
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
            logging.basicConfig(
                format='%(levelname)s:%(message)s',
                level=logging.DEBUG)
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
