# Steam Community Archiving Tool
# Usage: python dl_steam.py {APP_ID1} {APP_ID2} ...
#
# Archives various aspects of Steam Community for particular games/pages. It gets:
#  - Screenshots (and snippets of HTML describing them)
#  - Guides (and any images embedded in them)
#  - Discussion posts and replies
#  - Metadata for Workshop items
# 
# Overall, this script focuses on ensuring the data is retrieved *in some format.*
# It does not try to parse out much, and often leaves HTML snippets in their raw form.
# But, its intended purpose is to ensure that the information lives on. Retrieval niceties can come later.
#
# A sqlite database containing metadata and most text is placed in "data/{APP_ID}/meta.db"
# A snapshot of the HTML for the community hub homepage for that app is in "data/{APP_ID}/homepage.html"
# Images are sorted into guideIMG, screenshots, and workshopIMG folders depending on their source.
#
# This script tries to be idempotent: if you run it on the same app id multiple times you won't lose data,
# and hopefully it won't do too much redundant work (but it will re-crawl a lot, so keep that in mind)
#
# There are other aspects of Steam Community that this script doesn't yet grab. That functionality
# may be added in the future, but it's missing right now.
#
# This script depends a lot on specific HTML formatting in Steam's pages. I will try to
# keep it up to date, but if Steam makes changes it will break this (Hi Valve).
#
# Dependencies:
#  - Python 3
#

# NOTE: Another version of this script used a 3rd-party website to also download the files for workshop items.
# However, I found this to be unreliable, and was uneasy with using it long-term.


# These are some parameters for how many pages of the navigation search to crawl
# Most communities should stay within these limits, and we usually stop much earlier by getting to the last page
# However, if they need to be adjusted: here they are
MAX_SCREENSHOT_PAGE_COUNT = 10 * 1000
MAX_GUIDE_PAGE_COUNT = 500
MAX_DISCUSSION_PAGE_COUNT = 800
MAX_WORKSHOP_PAGE_COUNT = 8000   

import json

import os
import sys
import errno

import ssl

import math

import time
import traceback

import cgi

import http.client
import urllib.request
from urllib.request import urlparse
from urllib.parse import urlencode, quote, unquote

from socket import gaierror, timeout

import sqlite3

import re

def MaybeMakeDirectory(dirname):
    try:
        os.makedirs(dirname)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise    


def GetNewHttpConn(origin, doSSL=True):
    conn = {}
    conn['origin'] = origin
    conn['doSSL'] = doSSL
    if conn['doSSL']:
        conn['conn'] = http.client.HTTPSConnection(conn['origin'], timeout=50)
    else:
        conn['conn'] = http.client.HTTPConnection(conn['origin'], timeout=50)
    return conn

offIts404 = {}
    
offIts3XX = {}
    
def GetMessageWithRetries(conn, url, headersData={}, maxRetries = 13, outResponseHeaders = None, postData = None):
    timeout = 15
    for i in range(maxRetries):
        try:
            #print('Making request "%s"...' % url)
            if postData is None:
                conn['conn'].request("GET", url, headers=headersData)
            else:
                conn['conn'].request('POST', url, postData, headersData)
            res = conn['conn'].getresponse()
            #print('Got response')
            
            #print('Got %d status...' % res.status, file=sys.stderr)
            #print(res.headers, file=sys.stderr)
            
            if res is not None and res.status == 200:
                data = res.read()
                #print('READ DATA')
                
                if outResponseHeaders is not None:
                    for header,value in res.getheaders():
                        outResponseHeaders[header] = value
                
                return data
            elif res.status == 303 or res.status == 302 or res.status == 301:
                data = res.read()
                
                print('Location: ' + res.getheader('Location'))
                
                offIts3XX['Location'] = res.getheader('Location')
                
                return None#offIts3XX
            elif res.status == 403:
                print('Got status 403...waiting...')
                time.sleep(5.0)
                return None
            elif res.status == 404:
                print('Got status 404')
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


commConn = GetNewHttpConn('steamcommunity.com')
userImgConn = GetNewHttpConn('steamuserimages-a.akamaihd.net')


def GetBestImageURLFromScreenshotSrcSet(srcSet):
    bestURL = None
    bestWidth = 0
    parts = srcSet.split(',')
    for part in parts:
        subparts = part.strip().split(' ')
        width = int(subparts[1].strip()[:-1])
        if width > bestWidth:
            bestURL = subparts[0].strip()
            bestWidth = width
    
    return bestURL

def GetAppMetadataDBReady(appID):
    MaybeMakeDirectory('data/%s' % appID)
    db = sqlite3.connect('data/%s/meta.db' % appID)
    
    db.execute('CREATE TABLE IF NOT EXISTS Screenshots(id VARCHAR(255) PRIMARY KEY, htmlStuff TEXT, imgURL VARCHAR(255));')
    db.execute('CREATE TABLE IF NOT EXISTS Guides(id VARCHAR(255) PRIMARY KEY, pageHTML TEXT);')
    db.execute('CREATE TABLE IF NOT EXISTS GuideImages(guideID VARCHAR(255), imageURL VARCHAR(255), PRIMARY KEY(guideID, imageURL));')
    db.execute('CREATE TABLE IF NOT EXISTS Discussions(discussionID VARCHAR(255) PRIMARY KEY, pageHTML TEXT);')
    db.execute('CREATE TABLE IF NOT EXISTS DiscussionReplies(commentID VARCHAR(255) PRIMARY KEY, discussionID VARCHAR(255), contents TEXT);')
    db.execute('CREATE TABLE IF NOT EXISTS WorkshopItems(itemID VARCHAR(255) PRIMARY KEY, pageHTML TEXT);')
    db.execute('CREATE TABLE IF NOT EXISTS WorkshopItemImages(itemID VARCHAR(255), imageURL VARCHAR(255), PRIMARY KEY(itemID, imageURL));')
    db.execute('CREATE INDEX IF NOT EXISTS GuideImagesGuideIDIndex ON GuideImages(guideID);')
    db.execute('CREATE INDEX IF NOT EXISTS DiscussionRepliesDiscussionIDIndex ON DiscussionReplies(discussionID);')
    db.execute('CREATE INDEX IF NOT EXISTS WorkshopItemImagesWorkshopItemIndex ON WorkshopItemImages(itemID);')
    db.commit()
    
    return db
    
screenshotSrcSetReg = re.compile(r'srcset="([^"]*)"')
screenshotIDReg = re.compile(r'id="apphub_Card_([a-zA-Z0-9]*)"')

def DownloadScreenshot(appID, screenshotID, imgURLPath):
    dirname = 'data/%s/screenshots' % appID
    MaybeMakeDirectory(dirname)
    
    filename = '%s/%s.png' % (dirname, screenshotID)
    if os.path.exists(filename):
        print('Skipping "%s", already grabbed' % imgURLPath)
    else:
        data = GetMessageWithRetries(userImgConn, imgURLPath)
        if data is None:
            print('Could not download "%s"...' % imgURLPath)
        else:
            with open(filename, 'wb') as f:
                f.write(data)


def ScrapeAppCommunityScreenshots(appID, db):
    for pageIdx in range(1,MAX_SCREENSHOT_PAGE_COUNT):
        url = '/app/%s/homecontent/?p=%d&screenshotspage=%d&numperpage=10&&browsefilter=mostrecent&appid=%s&appHubSubSection=2&appHubSubSection=2&searchText=' % (appID, pageIdx, pageIdx, appID)
        
        data = GetMessageWithRetries(commConn, url)
        print('Grabbed page %d of screenshots for app "%s"' % (pageIdx, appID))
        if data is None:
            print('Welp, couldn\'t download steam community screenshots page %d for "%s"' % (pageIdx, appID))
        else:
            text = data.decode('utf-8', 'backslashreplace')
            
            elements = text.split('<div class="apphub_Card modalContentLink interactable"')
            
            if len(elements) <= 1:
                print('Reached final page')
                break
            
            db.execute('BEGIN TRANSACTION;')
            
            print('Got %d elements...' % len(elements))
            for element in elements[1:]:
                srcSetMatch = screenshotSrcSetReg.search(element)
                if not srcSetMatch:
                    print('Possibly missing a screenshot: couldn\'t find srcset tag.')
                    continue
                
                srcSet = srcSetMatch.group(1)
                screenshotID = screenshotIDReg.search(element).group(1)
                bestImgURL = GetBestImageURLFromScreenshotSrcSet(srcSet)
                print('Got ID "%s" img src: "%s"' % (screenshotID, bestImgURL))
                
                expectedStart = 'https://steamuserimages-a.akamaihd.net'
                if bestImgURL.startswith(expectedStart):
                    imgURLPath = bestImgURL[len(expectedStart):]
                    DownloadScreenshot(appID, screenshotID, imgURLPath)
                else:
                    print('Cannot download "%s", unexpected domain' % bestImgURL)
                
                db.execute('INSERT OR IGNORE INTO Screenshots VALUES(?,?,?);', (screenshotID, element, bestImgURL))

            db.execute('COMMIT;')
            db.commit()

searchGuideReg = re.compile(r'href="https:\/\/steamcommunity.com\/sharedfiles\/filedetails\/\?id=([0-9]*)"')
    
guideHHTMLImageLinkReg = re.compile(r'<a href="https:\/\/steamuserimages-a\.akamaihd\.net(\/ugc\/[A-Z0-9]*\/[A-Z0-9]*\/)" class="modalContentLink"')
def ScrapeGuideTextForImages(appID, db, guideID, guideText):
    dirname = 'data/%s/guideIMG/%s' % (appID, guideID)
    
    for match in guideHHTMLImageLinkReg.finditer(guideText):
        imageLink = match.group(1)
        imgParts = imageLink.split('/')
        # Get the last two non-empty parts of the path
        filename = '%s/%s_%s.jpg' % (dirname, imgParts[-3], imgParts[-2])
        
        db.execute('INSERT OR IGNORE INTO GuideImages VALUES(?,?)', (guideID, imageLink))
        
        if os.path.exists(filename):
            print('Skipping "%s", already grabbed' % imageLink)
        else:
            print('DL "%s" -> "%s"' % (imageLink, filename))
            data = GetMessageWithRetries(userImgConn, imageLink)
            if data is None:
                print('Could not download "%s"...' % imageLink)
            else:
                MaybeMakeDirectory(dirname)
                with open(filename, 'wb') as f:
                    f.write(data)

def ScrapeAppCommunityGuides(appID, db):
    for pageIdx in range(1, MAX_GUIDE_PAGE_COUNT):
        searchURL = '/app/%s/guides/?browsefilter=mostrecent&p=%d' % (appID, pageIdx)
        data = GetMessageWithRetries(commConn, searchURL)
        if data is None:
            print('Could not download page %d of "%s"' % (pageIdx, appID))
        else:
            text = data.decode('utf-8', 'backslashreplace')
            
            print('Got page %d of guides for app "%s"' % (pageIdx, appID))
            
            db.execute('BEGIN TRANSACTION;')
            
            counter = 0
            for match in searchGuideReg.finditer(text):
                guideID = match.group(1)
                guideURL = '/sharedfiles/filedetails/?id=%s' % guideID
                guideData = GetMessageWithRetries(commConn, guideURL)
                if guideData is None:
                    print('Could not download guide "%s" for app "%s"' % (guideID, appID))
                else:
                    print('Got guide "%s" for app "%s"' % (guideID, appID))
                    guideText = guideData.decode('utf-8', 'backslashreplace')
                    ScrapeGuideTextForImages(appID, db, guideID, guideText)
                    db.execute('INSERT OR IGNORE INTO Guides VALUES(?,?)', (guideID, guideText))
                    
                counter += 1
                
                
            db.execute('COMMIT;')
            db.commit()
                    
            if counter <= 0:
                print('Reached last page of guides')
                break

discussionSearchLinkReg = re.compile(r'<a class="forum_topic_overlay" href="https:\/\/steamcommunity\.com(\/app\/[0-9]*\/discussions\/0\/([0-9]*)\/)">')
discussionCommentCountReg = re.compile(r'[0-9]*</span> of <span id="commentthread_ForumTopic_[0-9_]*_pagetotal">([0-9,]*)</span> comments')
discussionCommentIDReg = re.compile(r'id="comment_([0-9]*)"')

def ScrapeDiscussionPageForReplies(appID, db, discussionID, pageHTML):
    replyParts = pageHTML.split('<div class="commentthread_comment responsive_body_text   "')[1:]
    for reply in replyParts:
        match = discussionCommentIDReg.search(reply)
        if match:
            commentID = match.group(1)
            db.execute('INSERT OR IGNORE INTO DiscussionReplies VALUES(?,?,?);', (commentID, discussionID, reply))
        else:
            print('Could not find comment id...')
                
def ScrapeDiscussionURL(appID, db, discussionID, discussionURL):
    firstPageData = GetMessageWithRetries(commConn, discussionURL)
    if firstPageData is None:
        print('Could not download first page of discussion "%s" for app "%s"' % (discussionID, appID))
    else:
        firstPageText = firstPageData.decode('utf-8', 'backslashreplace')
        
        db.execute('INSERT OR IGNORE INTO Discussions VALUES(?,?);', (discussionID, firstPageText))
        
        match = discussionCommentCountReg.search(firstPageText)
        if match:
            commentCountText = match.group(1)
            print('Discussion "%s" for app "%s" has "%s" comments' % (discussionID, appID, commentCountText))
            
            commentCount = int(commentCountText.replace(',', ''))
            commentsPerPage = 15
            pageCount = (commentCount + commentsPerPage - 1) // commentsPerPage
            
            print('Grabbing %d pages...' % pageCount)
            
            ScrapeDiscussionPageForReplies(appID, db, discussionID, firstPageText)
            
            for i in range(2, pageCount+1):
                pageData = GetMessageWithRetries(commConn, discussionURL + '?ctp=%d' % i)
                if pageData is None:
                    print('Could not download page %d of discussion "%s" for app "%s"' % (i, discussionID, appID))
                else:
                    print('Scraping page %d...' % i)
                    ScrapeDiscussionPageForReplies(appID, db, discussionID, pageData.decode('utf-8', 'backslashreplace'))
        else:
            print('Could not find comment count for discussion "%s" for app "%s"' % (discussionID, appID))
                

def ScrapeAppCommunityDiscussion(appID, db):
    for pageIdx in range(1, MAX_GUIDE_PAGE_COUNT+1):
        searchURL = '/app/%s/discussions/?fp=%d' % (appID, pageIdx)
        data = GetMessageWithRetries(commConn, searchURL)
        if data is None:
            print('Could not download page %d of "%s" discussions' % (pageIdx, appID))
        else:
            text = data.decode('utf-8', 'backslashreplace')
            
            print('Got page %d of discussions for app "%s"' % (pageIdx, appID))
            
            db.execute('BEGIN TRANSACTION;')
            
            counter = 0
            
            for match in discussionSearchLinkReg.finditer(text):
                discussionURL = match.group(1)
                discussionID = match.group(2)
                ScrapeDiscussionURL(appID, db, discussionID, discussionURL)
                counter += 1
            
            db.execute('COMMIT;')
            db.commit()
            
            if counter <= 0:
                print('Past last page of discussions')
                break
                
def ScrapeAppCommunityHomePage(appID):
    path = '/app/%s' % appID
    filename = 'data/%s/homepage.html' % appID
    if not os.path.exists(filename):
        data = GetMessageWithRetries(commConn, path)
        with open(filename, 'wb') as f:
            f.write(data)
   
workshopItemImageReg = re.compile(r"<a onclick=\"ShowEnlargedImagePreview\( 'https:\/\/steamuserimages-a.akamaihd.net(\/ugc\/[0-9A-Za-z]*\/[0-9A-Za-z]*\/)' \);\">")
   
def ScrapeWorkshopItemPageForImages(appID, db, itemID, pageHTML):
    dirname = 'data/%s/workshopIMG/%s' % (appID, itemID)
    
    for match in workshopItemImageReg.finditer(pageHTML):
        imageLink = match.group(1)
        imgParts = imageLink.split('/')
        # Get the last two non-empty parts of the path
        filename = '%s/%s_%s.jpg' % (dirname, imgParts[-3], imgParts[-2])
        
        db.execute('INSERT OR IGNORE INTO WorkshopItemImages VALUES(?,?)', (itemID, imageLink))
        
        if os.path.exists(filename):
            print('Skipping "%s", already grabbed' % imageLink)
        else:
            print('DL "%s" -> "%s"' % (imageLink, filename))
            data = GetMessageWithRetries(userImgConn, imageLink)
            if data is None:
                print('Could not download "%s"...' % imageLink)
            else:
                MaybeMakeDirectory(dirname)
                with open(filename, 'wb') as f:
                    f.write(data)
        
   
# TODO: Grab description, discussions, comments (<div class="commentthread_comment_content">), change notes
def ScrapeAppCommunityWorkshopItemMetadata(appID, db, itemID):
    print('Scraping item "%s" for app "%s"' % (itemID, appID))
    itemURL = '/sharedfiles/filedetails/%s' % itemID
    data = GetMessageWithRetries(commConn, itemURL)
    if data is None:
        print('Could not get main page for item "%s" on app "%s"' % (itemID, appID))
    else:
        text = data.decode('utf-8', 'backslashreplace')
        db.execute('INSERT OR IGNORE INTO WorkshopItems VALUES(?,?);', (itemID, text))
        
        ScrapeWorkshopItemPageForImages(appID, db, itemID, text)
        
                
workshopSearchItemsReg = re.compile(r'<a href="https:\/\/steamcommunity.com\/sharedfiles\/filedetails\/\?id=([0-9]*)&searchtext="><div class="workshopItemTitle ellipsis">')
            
        
def ScrapeAppCommunityWorkshopMetadata(appID, db):
    for pageIdx in range(1, MAX_WORKSHOP_PAGE_COUNT + 1):
        searchURL = '/workshop/browse/?appid=%s&browsesort=mostrecent&section=readytouseitems&actualsort=mostrecent&p=%d' % (appID, pageIdx)
        data = GetMessageWithRetries(commConn, searchURL)
        if data is None:
            print('Could not get search page %d for workshop items for app %s' % (pageIdx, appID))
            break
        else:
            db.execute('BEGIN TRANSACTION;')
            
            counter = 0
        
            text = data.decode('utf-8', 'backslashreplace')
            for match in workshopSearchItemsReg.finditer(text):
                workshopItemID = match.group(1)
                ScrapeAppCommunityWorkshopItemMetadata(appID, db, workshopItemID)
                
                # MARK: Downloads the actual files from workshop
                #GrabWorkshopFilesForItem(appID, workshopItemID)
                
                counter += 1
                
            db.execute('COMMIT;')
            db.commit()
            
            if counter == 0:
                print('Reached last page of workshop')
                break
    
                
# TODO: Also grab homepage just for some additional info?
# TODO: Grab videos (YT link + comments)
# TODO: Grab comments on screenshots?
# TODO: Grab workshop discussions
def ScrapeAppCommunity(appID):
    if str(int(appID)) != appID:
        print('Incorrect app id: "%s"' % appID)
        return

    db = GetAppMetadataDBReady(appID)
    ScrapeAppCommunityHomePage(appID)
    ScrapeAppCommunityWorkshopMetadata(appID, db)
    ScrapeAppCommunityScreenshots(appID, db)
    ScrapeAppCommunityGuides(appID, db)
    ScrapeAppCommunityDiscussion(appID, db)
    
if len(sys.argv) < 2:
    print('Please specify at least one app id')
    
for arg in sys.argv[1:]:
    ScrapeAppCommunity(arg)
