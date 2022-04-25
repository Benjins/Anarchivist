# Tweet Archiving Tool
# Usage: python dl_tw.py {USERNAME1} {USERNAME2} ...
# 
# Archives an entire user's Twitter profile. It gets:
# - the text of the tweet
# - the time of the tweet
# - which tweet (if any) it is was replying to
# - Any photos and video in the tweet
# - Alt text of photos
# 
# All data (except images + videos) is stored in a {USERNAME}/tweets.db sqlite database
# Images are stored at "{USERNAME}/Images/{TWEET_ID}_(IMAGE_INDEX}.{IMAGE_EXT}"
# Videos are stored under "{USERNAME}/Videos/{TWEET_ID}_{VIDEO_INDEX}.{VIDEO_EXT}"

# DEPENDENCIES:
# Uses Python 3
#
# Ethics: Please do not download other people's tweets for ill purposes. If someone does not wish
# you to download or repost their tweets, please respect that unless there is a very compelling reason not to.
#
# Notes/Caveats:
# - There are bugs, I have only tested this on my system
# - It is fairly fragile, expecting Twitter's HTML in a particular format. I will try to keep it up to date.
# - It is currently synchronous. I'd like to rewrite it to be async, but that will take some time
# - You can run multiple instances of the script, but *only if they are downloading different users.* There can be
#   some contention even then, but overall they shouldn't stomp on each other's toes.
#

import encodings.idna

import ssl

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
import traceback

from subprocess import call

import re
        
def MaybeMakeDirectory(dirname):
    try:
        os.makedirs(dirname)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise    
#---------------------------------
    
class Redir301:
    url = ''
    def __init__(self, _url):
        self.url = _url

def GetNewHttpConn(origin, doSSL=True):
    conn = {}
    conn['origin'] = origin
    conn['doSSL'] = doSSL
    if conn['doSSL']:
        conn['conn'] = http.client.HTTPSConnection(conn['origin'], timeout=50)
    else:
        conn['conn'] = http.client.HTTPConnection(conn['origin'], timeout=50)
    return conn
    
def GetMessageWithRetries(conn, url, headersData={}, postData = None, maxRetries = 13, outHeaders = None):
    timeout = 15
    for i in range(maxRetries):
        try:
            #print('Making request "%s"...' % url)
            #print('Headers = "%s"' % str(headersData))
            if postData == None:
                conn['conn'].request("GET", url, headers=headersData)
            else:
                conn['conn'].request("POST", url, data=postData, headers=headersData)
                
            res = conn['conn'].getresponse()
            #print('Got response')
            
            #print('Got %d status...' % res.status, file=sys.stderr)
            #print(res.headers, file=sys.stderr)
            
            if res is not None and res.status == 200:
                data = res.read()
                #print('READ DATA')
                
                if outHeaders is not None:
                    for headerName, headerVal in res.getheaders():
                        outHeaders.append( (headerName, headerVal) )
                
                return data
            elif res.status == 303 or res.status == 302 or res.status == 301:
                data = res.read()
                
                print('Location: ' + res.getheader('Location'))
                
                return Redir301(res.getheader('Location'))
            elif res.status == 403:
                time.sleep(5.0)
                return None
            elif res.status == 404:
                return None
            else:
                print('Got status %d...' % res.status)
                time.sleep(timeout)
                timeout = timeout + 3
            
        except http.client.ResponseNotReady:
            print('Connection got ResponseNotReady, retrying...', file=sys.stderr)
            if conn['doSSL']:
                conn['conn'] = http.client.HTTPSConnection(conn['origin'], timeout=50)
            else:
                conn['conn'] = http.client.HTTPConnection(conn['origin'], timeout=50)
        except http.client.RemoteDisconnected:
            print('Connection got RemoteDisconnected, retrying...', file=sys.stderr)
            if conn['doSSL']:
                conn['conn'] = http.client.HTTPSConnection(conn['origin'], timeout=50)
            else:
                conn['conn'] = http.client.HTTPConnection(conn['origin'], timeout=50)
        except Exception as e:
            traceback.print_exc()
            print("Error, got exception: '%s'" % str(e), file=sys.stderr)
            conn['conn'] = http.client.HTTPSConnection(conn['origin'], timeout=50)
            time.sleep(timeout)
            timeout = timeout + 3

    return None


#--------------------------------------

IMGconn = GetNewHttpConn("pbs.twimg.com")
def DownloadURLIMG(url, headers={}):   
    return GetMessageWithRetries(IMGconn, url, headers)
        
Vidconn = GetNewHttpConn('video.twimg.com')
def DownloadURLVID(url, headers={}):   
    return GetMessageWithRetries(Vidconn, url, headers)
        
TWconn = GetNewHttpConn("twitter.com")
def DownloadURL(url, headers={}, outHeaders = None):   
    return GetMessageWithRetries(TWconn, url, headers, outHeaders=outHeaders)
    
APIconn = GetNewHttpConn("api.twitter.com")
def DownloadURLAPI(url, headers={}, ):   
    return GetMessageWithRetries(APIconn, url, headers)


USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36'
    
def GetGuestToken(baseURL):
    outHeaders = []
    data = DownloadURL(baseURL, headers = {'User-Agent': USER_AGENT, 'Cookie': 'guest_id=v1%3A159417106010407620; personalization_id=v1_eSHozHH8LPFDsnzpb7kZhQ=='}, outHeaders=outHeaders)

    for headerName,headerVal in outHeaders:
        if headerName.lower() == 'set-cookie':
            cookieParts = headerVal.split('=')
            #if cookieParts[0].strip() == 'gt':
            if cookieParts[0].strip() == 'guest_id':
                token = cookieParts[1].strip().split(';',1)[0].strip().replace('%3A', ':')
                print('Found guest token "%s"' % token)
                return token
    
    match = re.search(r'document\.cookie = decodeURIComponent\("gt=(\d+);', data.decode('utf-8'))
    if match:
        return match.group(1)
        
    print('No guest token in cookies or script tag...')
    return None
            
    
    
def DownloadMediaForTweet(db, username, tweetInfo):
    index = 0
    tweetID = tweetInfo['id_str']
    
    if 'extended_entities' not in tweetInfo:
        return
    
    for media in tweetInfo['extended_entities']['media']:
        try:
            if media['type'] == 'photo':
                altText = media['ext_alt_text']
                imgURL = media['media_url_https']
                imgURL = imgURL[len('https://pbs.twimg.com'):]

                # Convert it to a full-resolution URL
                # If there are already URL params, tack one on at the end,
                # Otherwise add it as a query string
                if '?' in imgURL:
                    imgURL += '&name=orig'
                else:
                    imgURL += '?name=orig'

                filename = './%s/Images/%s_%d.jpg' % (username, tweetID, index)
                
                if os.path.exists(filename):
                    print('Skipping download for "%s" because it already exists at "%s"' % (imgURL, filename))
                else:
                    imgData = DownloadURLIMG(imgURL)
                    if imgData is None:
                        print('Null image on tweet "%s"' % tweetID)
                    else:
                        with open(filename, 'wb') as f:
                            f.write(imgData)
                        
                db.execute('INSERT OR IGNORE INTO TweetImages(tweetID, url, mediaIndex, user, altText) VALUES(?,?,?,?,?)', (tweetID, imgURL, index, username, altText))
                
            elif media['type'] == 'video' or media['type'] == 'animated_gif':
                altText = media['ext_alt_text']
                mediaURL = None
                bestBitrate = -1
                for variant in media['video_info']['variants']:
                    if variant['content_type'] == 'video/mp4':
                        bitrate = int(variant['bitrate'])
                        if bitrate > bestBitrate:
                            mediaURL = variant['url']
                            bestBitrate = bitrate
                            
                filename = './%s/Videos/%s_%d.mp4' % (username, tweetID, index)
                
                mediaURL = mediaURL[len('https://video.twimg.com'):]
                
                if os.path.exists(filename):
                    print('Skipping download for "%s" because it already exists at "%s"' % (mediaURL, filename))
                else:
                    vidData = DownloadURLVID(mediaURL)
                    with open(filename, 'wb') as f:
                        f.write(vidData)
                
                
                db.execute('INSERT OR IGNORE INTO TweetVideos(tweetID, url, mediaIndex, user, altText) VALUES(?,?,?,?,?)', (tweetID, mediaURL, index, username, altText))
            else:
                raise RuntimeError('Weird type: "%s" on tweet "%s"' % (media['type'], tweetID))
        except KeyboardInterrupt:
            raise
        except RuntimeError:
            raise
        except:
            print('Error on some media or whatever for tweet "%s"' % tweetID)
    
        index += 1
    
    
def GetTweetIDsForUser_V2(db, username):
    query = 'from:%s' % username

    baseURL = '/search?' + urlencode({'f': 'live', 'lang': 'en', 'q': query, 'src': 'spelling_expansion_revert_click'})

    guestToken = GetGuestToken(baseURL)
    
    if guestToken is None:
        print('No guest token...')
        return
    
    # Authorization header is not secret, used by any web client
    headers = {
        'User-Agent': USER_AGENT,
        'Authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
        'Referer': baseURL,
        'X-Guest-Token': guestToken
    }
    
    params = {
        'include_profile_interstitial_type': '1',
        'include_blocking': '1',
        'include_blocked_by': '1',
        'include_followed_by': '1',
        'include_want_retweets': '1',
        'include_mute_edge': '1',
        'include_can_dm': '1',
        'include_can_media_tag': '1',
        'skip_status': '1',
        'cards_platform': 'Web-12',
        'include_cards': '1',
        'include_ext_alt_text': 'true',
        'include_quote_count': 'true',
        'include_reply_count': '1',
        'tweet_mode': 'extended',
        'include_entities': 'true',
        'include_user_entities': 'true',
        'include_ext_media_color': 'true',
        'include_ext_media_availability': 'true',
        'send_error_codes': 'true',
        'simple_quoted_tweets': 'true',
        'q': query,
        'tweet_search_mode': 'live',
        'count': '100',
        'query_source': 'spelling_expansion_revert_click',
        'cursor': None,
        'pc': '1',
        'spelling_corrections': '1',
        'ext': 'mediaStats,highlightedLabel',
    }
    
    TWITTER_VERSION = 2
    
    NUM_RETRIES_EMPTY_RESULTS = 20
    
    currentEmptyRetryCounter = 0
    
    
    cursor = None
    
    while True:
        if cursor is not None:
            params['cursor'] = cursor
        
        searchURL = '/2/search/adaptive.json?' + urlencode(params)
        data = DownloadURLAPI(searchURL, headers)
        
        if data is None:
            print('Hmmm...lets wait and retry guest token')
            time.sleep(10.0)
            headers['X-Guest-Token'] = GetGuestToken(baseURL)
            time.sleep(1.0)
            data = DownloadURLAPI(searchURL, headers)
            
        if data is None:
            print('Okay, that didnt work either, lets just bail for now')
            break
        
        info = json.loads(data.decode('utf-8', 'backslashreplace'))
    
        db.execute('BEGIN TRANSACTION;')
        
        count = 0
        newCount = 0
        
        try:    
            newCursor = None
            for instruction in info['timeline']['instructions']:
                if 'addEntries' in instruction:
                    entries = instruction['addEntries']['entries']
                elif 'replaceEntry' in instruction:
                    entries = [instruction['replaceEntry']['entry']]
                else:
                    continue
                for entry in entries:
                    if entry['entryId'].startswith('sq-I-t-'):
                        try:
                            tweet = info['globalObjects']['tweets'][entry['content']['item']['content']['tweet']['id']]
                            tweetID = tweet['id_str']
                            content = tweet['full_text']
                            full_tweet_json = json.dumps(tweet)
                            isNew = db.execute('INSERT OR IGNORE INTO TweetData VALUES(?,?,?,?,?,?,?)', (tweetID, username, content, tweet['created_at'], tweet['in_reply_to_status_id_str'], full_tweet_json, TWITTER_VERSION)).rowcount
                            count += 1
                            
                            if isNew:
                                newCount += 1
                                DownloadMediaForTweet(db, username, tweet)
                        except KeyboardInterrupt:
                            raise
                        except:
                            print('Got exception in entry "%s", skipping to next' % entry['entryId'])
                            traceback.print_exc()
                            pass
                        
                    elif entry['entryId'] == 'sq-cursor-bottom':
                        newCursor = entry['content']['operation']['cursor']['value']

        except:
            print('Encountered exception, rolling back')
            db.execute('ROLLBACK;')
            raise
        else:
            db.execute('COMMIT;')
            db.commit()
            print('Added %d tweets (%d new)' % (count, newCount))


        if not newCursor:
            # End of pagination
            break
            
        if newCursor == cursor or count == 0:
            currentEmptyRetryCounter += 1
            if currentEmptyRetryCounter > NUM_RETRIES_EMPTY_RESULTS:
                print('We have failed to advance through the search even after %d retries on the same cursor. This is probably the end of the search' % currentEmptyRetryCounter)
                break
            else:
                print('Results were empty, or cursor failed to make progress. Will retry shortly (%d/%d)' % (currentEmptyRetryCounter, NUM_RETRIES_EMPTY_RESULTS))
                time.sleep(0.2)
            
        else:
            currentEmptyRetryCounter = 0
            
        cursor = newCursor


def SetupDatabaseAndStuffForUser(user):
    dirname = './%s' % user
    MaybeMakeDirectory(dirname)
    MaybeMakeDirectory(dirname + '/Images')
    MaybeMakeDirectory(dirname + '/Videos')

    db = sqlite3.connect('%s/tweets.db' % dirname, timeout=60.0)

    # Set up Tweet Database
    # TODO: Unique constraint on TweetImages/TweetVideo
    db.execute('CREATE TABLE If NOT EXISTS TweetData(tweetID INTEGER PRIMARY KEY, username VARCHAR(255), data TEXT, timestamp VARCHAR(255), replyTo INTEGER, fullTweetJSON TEXT, version INTEGER);')
    db.execute('CREATE TABLE If NOT EXISTS TweetImages(id INTEGER PRIMARY KEY AUTOINCREMENT, tweetID INTEGER, url VARCHAR(255), mediaIndex INTEGER, user VARCHAR(255), altText TEXT);')
    db.execute('CREATE TABLE If NOT EXISTS TweetVideos(id INTEGER PRIMARY KEY AUTOINCREMENT, tweetID INTEGER, url VARCHAR(255), mediaIndex INTEGER, user VARCHAR(255), altText TEXT);')
    db.execute('CREATE INDEX If NOT EXISTS TweetIDIndex ON TweetData (tweetID);')
    db.execute('CREATE INDEX If NOT EXISTS TweetDataUsernameIndex ON TweetData (username);')
    db.execute('CREATE INDEX If NOT EXISTS TweetImageUsernameIndex ON TweetImages (user);')
    db.execute('CREATE INDEX If NOT EXISTS TweetVideoUsernameIndex ON TweetVideos (user);')

    db.commit()

    print('Done setting up database for "%s"' % user)
    
    return db


def DownloadUserTweets_V2(user):
    db = SetupDatabaseAndStuffForUser(user)
    GetTweetIDsForUser_V2(db, user)

    
if len(sys.argv) <= 1:
    print('Please provide usernames as cmd line args')
else:
    for username in sys.argv[1:]:
        DownloadUserTweets_V2(username.strip())


