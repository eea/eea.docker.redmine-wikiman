import json
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
from pathlib import Path
import yaml
from zipfile import ZipFile
from shutil import rmtree
from shutil import move
from git import Repo
from datetime import datetime


def get_operation(rancherUrl, rancherAccessKey,
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
    f = urllib.request.urlopen(url)
    rawdata = f.read()
    f.close()
    return json.loads(rawdata)

def get_raw(rancherUrl, rancherAccessKey,
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
    try:
        f = urllib.request.urlopen(url)
        rawdata = f.read()
        f.close()
    except urllib.error.URLError as exception:
        if "Operation timed out" in exception:
            logging.warning(exception)
            f = urllib.request.urlopen(url)
            rawdata = f.read()
            f.close()
        else:
            logging.error(exception)
            return
    return rawdata


def getKey(instance):
    """ Return the key to sort on """
    return instance['name']




def backup_configuration(path, stack, instance, rancher_name, compose):
    
    try:
        rmtree(path)   
    except:
        logging.debug('Directory '+path+' did not exist')

    Path(path).mkdir(parents=True, exist_ok=True)

    #create only if necessary
   
    if compose:    
        compose_file=path + '/compose.zip'

        f = open(compose_file, "w+b")
        binary_format = bytearray(compose)
        f.write(binary_format)
        f.close()
        
        with ZipFile(compose_file, 'r') as zipObj:
            # logging.info(zipObj)
            # Extract all the contents of zip file in different directory
            zipObj.extractall(path)

        os.remove(compose_file)
        
    else:
        logging.info(path)
        logging.info("NO compose file received")

    f = open(path + '/README.md','w')
    info = '# '+ stack.get("name",'')+'\n'
    info = info + '\n## Stack information\n* **Rancher:** '+rancher_name+'\n* **Env:** '+ instance
    
    if stack.get('externalId'):
        info = info + '\n* **Catalog:** `' + stack.get("externalId") + '`'
    if stack.get('group'):
        info = info + '\n* **Tags:** `' + stack.get("group") + '`'
    if stack.get('description'):
        info = info + '\n* **Description:** `' + stack.get("description")+'`'

    flag=''
    if (stack['system']):
        flag=' --system'
        info = info + '\n* **Infrastructure/system stack:** `YES`'

    if stack.get('externalId'):
        info = info + '\n\n## Restore in rancher ( catalog )\n'
        info = info + '* Use `rancher-cli`, at least 0.6.14 - for example https://github.com/rancher/cli/releases/download/v0.6.14/rancher-linux-amd64-v0.6.14.tar.gz\n'
        info = info + '* Run `rancher config` to ensure the correct rancher environment\n'
        info = info + '* Run command: \n> `rancher catalog install '+stack['externalId']+flag+' --name '+ stack['name'] +' --answers answers.yml`\n'
    else:
        info = info + '\n\n## Restore in rancher ( rancher-compose ) \n'
        info = info + 'Use `rancher-compose up -d` in the current directory, which has the same name as the stack and contains the `rancher-compose.yml` and `docker-compose.yml` files\n'

    f.write(info)
    f.close()

    if stack.get('environment'):
        f = open(path + '/answers.yml', "w")
        yaml.dump(stack['environment'], f, default_flow_style=False)   
        f.close()
    
    return

def init_repo(path, giturl):
    try:
        repo = Repo(path)
        origin = repo.remote(name='origin')
        origin.pull()
    except:
        repo = Repo.clone_from(giturl, path)
    return repo

def save_repo(repo, repo_path, message):
    now = datetime.now()
    repo.git.add(repo_path, '-A')
    if repo.is_dirty():
        repo.index.commit(message + now.strftime("%d-%m-%Y %H:%M"))
        origin = repo.remote(name='origin')
        origin.push()
        logging.info("Updated repo " + repo_path)
    else:
        logging.debug("Nothing to update " + repo_path)


def main(dryrun):

    rancher_configs = os.getenv('RANCHER_CONFIG')
    repos_location = os.getenv('REPO_PATH','GIT')
    git_url_auth = os.getenv('GITLAB_CONFIG') 

    for rancher_config in rancher_configs.split():
        rancher_configuration = rancher_config.split(",")
        rancherUrl = rancher_configuration[0]
        rancherApiUrl = rancherUrl + "/v2-beta"
        rancherAccessKey = rancher_configuration[1]
        rancherSecretKey = rancher_configuration[2]
        logging.info(rancherUrl)

        rancher_name=rancherUrl.replace('https://','').replace('kvm-rancher-','').replace('.eea.europa.eu','').upper()

        try:
            structdata = get_operation(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                rancherApiUrl +
                "/projects")
        except BaseException:
            raise RuntimeError("There was a problem reading from Rancher")

        for project in sorted(structdata['data'], key=getKey):
            if project['state'] != 'active':
                continue
            environment = project['id']
            envURL = rancherUrl + "/env/" + environment
            logging.info("Retrieving %s - %s", environment, project['name'])

            stackUrl = rancherApiUrl + "/projects/" + environment
            structdata = get_operation(
                rancherApiUrl,
                rancherAccessKey,
                rancherSecretKey,
                stackUrl + "/stacks")

            env=project['name'].split('(')[0].replace(' ','')

            repo_path = repos_location + '/' + rancher_name + '/' + env 
            
            Path(repo_path).mkdir(parents=True, exist_ok=True)
            
            try:
                repo = init_repo(repo_path, git_url_auth + '/' + rancher_name.lower()+'/'+env+'.git')
            except:
                logging.info("Received error while initiating the git repo, will skip RANCHER ENV")
                continue
            existing_stacks = '.git  00-infrastructure-stacks 99-archived-stacks'
            
            for instance in sorted(structdata['data'], key=getKey):
                link = envURL + "/apps/stacks/" + instance['id']
                count = sum(p['name'] == instance['name'] for p in structdata['data']) 

                if count > 1:
                    instance['name'] = instance['name']+'-'+instance['id']

                if instance.get('links').get('composeConfig'):
                    try:
                        composeFile = get_raw(
                            rancherApiUrl,
                            rancherAccessKey,
                            rancherSecretKey,
                            instance['links']['composeConfig'])
                    except urllib.error.HTTPError as exception:
                        logging.warning(instance['links']['composeConfig'])
                        logging.warning(project['name']+' '+instance['name'])
                        logging.warning(exception)
                        continue
                    except urllib.error.URLError as exception:
                        logging.error(instance['links']['composeConfig'])
                        logging.error(project['name']+' '+instance['name'])
                        logging.error(exception)
                        existing_stacks += ' '+instance['name']
                        continue                       
                else:
                    composeFile = None
                                
                if (instance['system']):
                    stack_path = '00-infrastructure-stacks/' + instance['name']
                else:
                    stack_path = instance['name']
                
                path = repo_path + '/' + stack_path
                if  instance['name'] == 'default':
                    logging.info(instance)
                backup_configuration(path, instance, env, rancher_name, composeFile)
                if not dryrun:
                    save_repo(repo, stack_path, 'Backup '+instance['name']+' on ')

                existing_stacks += ' '+instance['name']

            for check_path in [repo_path,repo_path+'/00-infrastructure-stacks']:
                for i in os.listdir(check_path):
                    if i not in existing_stacks.split():
                        logging.info("Found stack directory that does not exist in rancher - "+i+", will move it to archive")
                        now = datetime.now()
                        move(check_path+'/'+i,check_path+'/99-archived-stacks/'+i+now.strftime("-%Y%m%d-%H%M"))
            if dryrun:
                logging.info("Run without commiting, you can review the files")
            else:
                save_repo(repo, '.' , 'Rancher backup on ')


if __name__ == '__main__':
    dryrun = False

    try:
        opts, args = getopt.getopt(sys.argv[1:], "vn")
    except getopt.GetoptError as err:
        sys.exit(2)
    logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.INFO)
    for o, a in opts:
        if o == "-v":
            logging.basicConfig(
                format='%(levelname)s:%(message)s',
                level=logging.DEBUG)
        if o == "-n":
            dryrun = True
            
    main(dryrun)        
