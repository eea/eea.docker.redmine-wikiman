import json
from redminelib import Redmine
import requests
import datetime
import re
import svn.remote
import sys
import os


server = os.getenv('WIKI_SERVER','')
apikey = os.getenv('WIKI_APIKEY','')
projectName = os.getenv('WIKI_PROJECT','')
pageName = os.getenv('WIKI_CONTAINERS_PAGE','')
svnuser = os.getenv('SVN_USER','')
svnpassword = os.getenv('SVN_PASSWORD','')

server = Redmine(server, key=apikey, requests={'verify': True})
project = server.project.get(projectName)

list = []
for page in project.wiki_pages:
  if ( str(getattr(page, 'parent', None)) == pageName ):
      list.append(page)

list2 = []
for page in project.wiki_pages:
  for child in list:
    if ( str(getattr(page, 'parent', None)) == str(child) ):
        list2.append(page)
     
list.extend(list2)

list_pages = []
for page in list:
   if ( getattr(page, 'text', None).find('DeploymentRepoURL') > 0 ):
     list_pages.append(page)

for page in list_pages:
  print "Starting with " + str(page)
  lines = page.text.splitlines()
  repos = [x for x in lines if 'DeploymentRepoURL:' in x]
  docker_images = {}
  for line in repos:
    url = line.replace('DeploymentRepoURL:','').strip()
    dockerfile = ""
    print "Deployment url " + url 
    try:
      if 'https://github.com/' in url:
        api = url.replace('https://github.com/','https://api.github.com/repos/').replace('tree/master', 'contents')
        response = requests.get(api)
        filter_dirs = [x for x in response.json() if x['type'] == 'dir']
        biggest = str(max(filter_dirs, key=lambda x: int(x['name']))['name'])
        response2 = requests.get(api.strip('/')+"/"+biggest)
        filter_dc = [x for x in response2.json() if 'docker-compose' in str(x['name']).lower()]
        response3 = requests.get(str(filter_dc[0]['url']), headers={"Accept": "application/vnd.github.VERSION.raw"})
        dockerfile = response3.text
      else:
       if 'https://eeasvn.eea.europa.eu/' in url:
          url = line.strip().split(' ')[1]
          r = svn.remote.RemoteClient(url, username=svnuser, password=svnpassword)
          for x in r.list():
            if str(x).lower() == 'docker-compose.yml':
              dockerfile = r.cat(x)
      
          if not dockerfile:
            for rel_path, e in r.list_recursive():
              if str(e['name']).lower() == 'docker-compose.yml':
                dockerfile = r.cat(rel_path+'/'+e['name'])
                break
    except:
      print "There was a problem accessing the DeploymentRepoURL docker-compose.yml"
    
    if not dockerfile:
      docker_images = {}
      print "There was a mistake, no docker-compose.yml file found"
      break
    
    lines = dockerfile.splitlines()
    images = [x for x in lines if 'image: ' in x]
    for image in images:
      name = image.strip().split(' ')[1].strip('"').strip("'")
      # print name
      if name not in docker_images:
        docker_image = name.split(':')[0]
        response4 = requests.get("https://hub.docker.com/api/build/v1/source/?image=" + docker_image)
        github_url = ''
        dockerhub_url = ''
        if docker_image.find('/') > 0:
          dockerhub_url = 'https://hub.docker.com/r/' + docker_image
          # print dockerhub_url
          if response4.status_code == 200 and response4.json().get('objects'):
            github_url = 'https://github.com/' + response4.json()['objects'][0]['owner'] + '/' + response4.json()['objects'][0]['repository']
            # print github_url
        else:
          dockerhub_url = 'https://hub.docker.com/r/_/' + docker_image
          # print dockerhub_url
        docker_images[name] = [ github_url, dockerhub_url ]
    #print docker_images
 
  if not docker_images:
    print "No docker images extracted, will continue"
    continue

  #text = "\nh2. Source code information\n\n"
  comment = "\n\n??This section of the wiki was generated automatically from the DeploymentRepoURL on " + datetime.date.today().strftime("%Y-%m-%d") +", please don't edit it manually.??\n\n"
  text = ""
  # print sorted(docker_images)
  for name in sorted(docker_images):
    text = text + '* *"' + name + '":' + docker_images[name][1] + '*'
    if docker_images[name][0]:
        text = text + ' | "Source code":' + docker_images[name][0]
    text = text + "\n"
  text = text + "\n"	
  new_text = ""
  pagetext = page.text + "h2. Temp\n"
  section = re.findall(r'(?<=h2. Source code information).+?(?=h2)', pagetext, re.DOTALL)
  if section:
    images_list = re.findall(r'(?<=\*).+?(?=$)', section[0], re.DOTALL)
    replace_section = ""
    if images_list:
     # print "found list of images"
     # print "-------------------------------"
     #  print images_list[0]
      new_images_list = re.findall(r'(?<=\*).+?(?=$)', comment + text, re.DOTALL)
     # print "-------------------------------"
     #  print new_images_list[0]
     #  print "-------------------------------"

      if new_images_list[0].rstrip() == images_list[0].rstrip(): 
        print "Nothing to update"
      else:
        replace_section = "yes"
    else:
      replace_section = "yes"

    if replace_section:
      print "Replaced the source code information text"
      new_text = page.text.replace(section[0],comment + text)
  else:
    new_text = page.text + "\n\nh2. Source code information" + comment + text
    print "Added new section"
	 
  if new_text:
    print "Will save now"
    new_text = new_text.replace('\n<div id="wiki_extentions_header">\n\n{{last_updated_at}} _by_ {{last_updated_by}}\n\n</div>\n\n','')
    page.text = new_text
    page.save()

