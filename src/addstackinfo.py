from redminelib import Redmine
import re
import sys
import os
import logging
import getopt

server = os.getenv('WIKI_SERVER','')
apikey = os.getenv('WIKI_APIKEY','')
projectName = os.getenv('WIKI_PROJECT','')
pageName = os.getenv('WIKI_PAGE','Applications')
stackwiki = os.getenv('WIKI_STACKS_PAGE','Rancher_stacks')

try:
  opts, args = getopt.getopt(sys.argv[1:], "dvn")
except getopt.GetoptError as err:
  sys.exit(2)

dryrun = False
debug = False

for o, a in opts:
  if o == "-v":
    debug = True
  if o == "-n":
    dryrun = True

if debug:
  logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.DEBUG)
else:
  logging.basicConfig(format='%(levelname)s: %(message)s', level=logging.INFO)    


logging.info("START")
if dryrun:
  logging.info("The script is running in dryrun mode, so no changes will be saved")    

server = Redmine(server, key=apikey, requests={'verify': True})

list1 = []

try:
  project = server.project.get(projectName)
  stack_wiki = server.wiki_page.get(stackwiki, project_id=projectName)
  stacks = stack_wiki.text.splitlines()
  for page in project.wiki_pages:
    if ( str(getattr(page, 'parent', None)) == pageName ):
        list1.append(page)
  list2 = []
  for page in project.wiki_pages:
    for child in list1:
      if ( str(getattr(page, 'parent', None)) == str(child) ):
        list2.append(page)
  list1.extend(list2)
except:
  logging.error("There was a problem reading from taskman wiki")
  logging.error(sys.exc_info())
  sys.exit(0)


list_pages = []
for page in list1:
   if ( getattr(page, 'text', None).find('Service location:') > 0 ):
     list_pages.append(page)

for page in list_pages:
  logging.info("Starting with " + str(page))
  lines = page.text.replace('\n<div id="wiki_extentions_header">\n\n{{last_updated_at}} _by_ {{last_updated_by}}\n\n</div>\n\n','').splitlines()
  next_line = ''
  text = ''
  something_changed = ''
  for line in lines:
    if next_line:
      if 'Rancher Stack URL:' in line:
        text = text + next_line + '\n'
        if line != next_line:
          something_changed = 'yes'
      else:
        text = text + next_line + '\n' + line + '\n'
        something_changed = 'yes'
      next_line = ''
    else:
      text = text + line + '\n'
    if 'Service location:' in line:
      url = line.replace('Service location:','').strip().lower().split(' ')[0].strip('/')
      if (url.find('(') >= 0):
        logging.warning("Could not extract url, check 'Service location' on page "+str(page))
        continue
      #print '\n'
      stack = ""
      if not url:
        logging.warning(str(page) + " Could not extract url")
        continue
      logging.debug("Found url " + url)
      # check normal url
      url_no_protocol = url.replace('http://','').replace('https://','')
      regex = re.compile(r' (https?://)?{}/?[^a-z/]'.format(url_no_protocol.replace('.','\.')), re.IGNORECASE)
      stack = list(filter(regex.search, stacks))
      if not stack:
        stack_name = url_no_protocol.replace('.europa.eu','').replace('.','-')
        stack = [x for x in stacks if '|"' + stack_name + '":' in x.lower()]
      if not stack:
        logging.debug("Could not find stack for url " + url)
        continue
      logging.debug(stack)
      next_line = "Rancher Stack URL: "
      regex = re.compile(r'^\|(".+?":.+?) \|.*$')
      for st in stack:
        stack_url = regex.match(str(st)).group(1) 
        next_line = next_line + stack_url + ', '
      next_line = next_line.strip().strip(',')
      logging.debug(next_line)
  if something_changed:
    if dryrun:
      logging.info("Changes found, but won't be saved")
      logging.debug(text)
    else:
      logging.info("Changes found, saving page with new information")
      page.text = text
      page.save()

logging.info("DONE")    
