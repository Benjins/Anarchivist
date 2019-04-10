# Twitch VOD chat archiving tool
# Usage: python dl_twitch_vod_chat.py {VOD_ID1} {VOD_ID2} ...
#
# Pretty straightforward: archives chat from Twitch VODs (includes commenter, text, and timestamp information)
#
# Stores JSON from each comment in a sqlite database, along with the UUID for the comment, and the video it was from.
# All other information will be in the JSON.
#
# Reasonably fast, cause it can get comments in bulk (and also...it's text).
#
# Dependencies:
#  - Python 3, but honestly there's not much to it could probably get it working on anything

import encodings.idna

import sys
import os
import errno

import json

import http.client

from urllib.parse import urlencode

import sqlite3

import os
import sys
import time

# This seems to be public, or at the very least not tied to an individual
# but rather shared by all desktop users
# May be subject to change in the future
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

def GetConn():
    return http.client.HTTPSConnection("api.twitch.tv")

TWconn = GetConn()
def DownloadURL(url, headers={}, postData=None):   
    global TWconn
    res = None
    retry = True
    ## If we get a ResponseNotReady error, it means our keepalive ran out
    ## In that case, we re-establish our connection and try again
    ## If we get a different error, it probably failed for real, so we just give up
    
    maxRetries = 15
    
    for i in range(maxRetries):
        try:
            ## TODO: Any headers that need to be set would go here
            if postData is None:
                TWconn.request("GET", url, headers=headers)
            else:
                TWconn.request('POST', url, urlencode(postData), headers=headers)
            res = TWconn.getresponse()
            break
        except http.client.ResponseNotReady:
            print("Re-establishing connection...")
            TWconn = GetConn()
        except Exception as e:
            print("Encountered other error...")
            ## For debugging, this is useful
            ## Otherwise, immortality has its perks
            #raise e
            TWconn = GetConn()
            #retry = False
            
        time.sleep(0.5)

    if res is not None and res.status == 200:   
        return res.read()
    elif res is not None and res.status == 301:
        print ('Location: ' + res.getheader('Location'))
        return None
    elif res is not None:
        print('Got status %d...' % res.status)
        return None
    else:
        return None

        
db = sqlite3.connect('vod_chat.db', timeout=60.0)

db.execute('CREATE TABLE IF NOT EXISTS ChatData(id VARCHAR(255) PRIMARY KEY, vodID VARCHAR(255), data TEXT);')
db.execute('CREATE INDEX IF NOT EXISTS ChatDataVodID ON ChatData(vodID);')
        
def DownloadChatForVOD(vodID):
    if vodID != str(int(vodID)):
        print('Invalid vod ID: "%s"' % vodID)
        return
    
    headers = {}
    headers["Client-ID"] = TWITCH_CLIENT_ID

    offsetSec = 0
    while True:
        print('Downloading comments from vod "%s" at offset %d' % (vodID, offsetSec))
        url = '/v5/videos/%s/comments?content_offset_seconds=%d' % (vodID, offsetSec)
        data = DownloadURL(url, headers=headers)
        
        if data is None:
            break
        else:
            info = json.loads(data.decode('utf-8'))
            
            if 'comments' not in info or len(info['comments']) <= 0:
                break
                
            print('   got %d comments' % len(info['comments']))
            
            db.execute('BEGIN TRANSACTION;')
            highestOffset = offsetSec + 1
            for comment in info['comments']:
                offset = int(comment['content_offset_seconds'])
                id = comment['_id']
                db.execute('INSERT OR IGNORE INTO ChatData VALUES(?,?,?)', (id, vodID, json.dumps(comment)))
                if offset > highestOffset:
                    highestOffset = offset
                    
                    
            offsetSec = highestOffset
        
            db.execute('COMMIT;')
            db.commit()
        
if len(sys.argv) < 2:
    print('Please specify at least one VOD id')

for arg in sys.argv[1:]:
    DownloadChatForVOD(arg)
 

