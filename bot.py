import re
import sqlalchemy
import solr
from simplemediawiki import MediaWiki
from editing import MusicBrainzClient
import pprint
import urllib
import time
import config as cfg

engine = sqlalchemy.create_engine(cfg.MB_DB)
db = engine.connect()
db.execute("SET search_path TO musicbrainz")

wp = MediaWiki('http://en.wikipedia.org/w/api.php')
wps = solr.SolrConnection('http://localhost:8983/solr/wikipedia')

mb = MusicBrainzClient(cfg.MB_USERNAME, cfg.MB_PASSWORD, cfg.MB_SITE)

"""

CREATE TABLE bot_wp_artist (
    gid uuid NOT NULL,
    processed timestamp with time zone DEFAULT now()
);

ALTER TABLE ONLY bot_wp_artist
    ADD CONSTRAINT bot_wp_artist_pkey PRIMARY KEY (gid);

CREATE VIEW v_artist_rgs AS
    SELECT acn.artist, count(*) AS count FROM (s_release_group rg JOIN artist_credit_name acn ON ((rg.artist_credit = acn.artist_credit))) WHERE (acn.artist > 2) GROUP BY acn.artist;

select a.id, ar.count into tmp_artists_with_wikipedia
from s_artist a join v_artist_rgs ar on a.id=ar.artist
left join l_artist_url l on l.entity0=a.id and
l.link in (select id from link where link_type=179) where a.id >2 and l.id is null;

SELECT a.id, a.gid, a.name
    FROM s_artist a
    JOIN v_artist_rgs ar ON a.id = ar.artist
    LEFT JOIN l_artist_url l ON l.entity0 = a.id AND l.link IN (SELECT id FROM link WHERE link_type=179)
WHERE a.id > 2 AND l.id IS NULL
ORDER BY ar.count DESC
LIMIT 1000
"""

query = """
SELECT a.id, a.gid, a.name
FROM tmp_artists_with_wikipedia ta
JOIN s_artist a ON ta.id=a.id
LEFT JOIN bot_wp_artist b ON a.gid = b.gid
WHERE b.gid IS NULL
ORDER BY ta.count DESC, a.id
LIMIT 10000
"""

query_artist_albums = """
SELECT rg.name
FROM s_release_group rg
JOIN artist_credit_name acn ON rg.artist_credit = acn.artist_credit
WHERE acn.artist = %s
UNION
SELECT r.name
FROM s_release r
JOIN artist_credit_name acn ON r.artist_credit = acn.artist_credit
WHERE acn.artist = %s
"""

def mangle_name(s):
    s = s.lower()
    return re.sub(r'\W', '', s, flags=re.UNICODE)

def join_albums(strings):
    result = 'album'
    if len(strings) > 1:
        result += 's'
    result += ' '
    strings = ['"%s"' % s for s in strings]
    if len(strings) < 2:
        result += strings[0]
    elif len(strings) < 5:
        result += ', '.join(strings[:-1])
        result += ' and %s' % strings[-1]
    else:
        result += ', '.join(strings[:4])
        result += ' and %s more' % (len(strings) - 4)
    return result

for a_id, a_gid, a_name in db.execute(query):
    print 'Looking up artist "%s" http://musicbrainz.org/artist/%s' % (a_name, a_gid)
    matches = wps.query(a_name, defType='dismax', qf='name', rows=30).results
    last_wp_request = time.time()
    for match in matches:
        title = match['name']
        delay = time.time() - last_wp_request
        if delay < 1.0:
            time.sleep(1.0 - delay)
        last_wp_request = time.time()
        resp = wp.call({'action': 'query', 'prop': 'revisions', 'titles': title, 'rvprop': 'content'})
        pages = resp['query']['pages'].values()
        if not pages or 'revisions' not in pages[0]:
            continue
        page = mangle_name(pages[0]['revisions'][0].values()[0])
        if 'disambiguationpages' in page:
            continue
        print ' * trying article "%s"' % (title,)
        page_title = pages[0]['title']
        found_albums = []
        albums = set([r[0] for r in db.execute(query_artist_albums, (a_id, a_id))])
        if a_name in albums:
            albums.remove(a_name)
        if not albums:
            continue
        for album in albums:
            mangled_album = mangle_name(album)
            if len(mangled_album) > 6 and mangled_album in page:
                found_albums.append(album)
        ratio = len(found_albums) * 1.0 / len(albums)
        print ' * ratio: %s, has albums: %s, found albums: %s' % (ratio, len(albums), len(found_albums))
        if ratio < 0.2:
            continue
        url = 'http://en.wikipedia.org/wiki/%s' % (urllib.quote(page_title.encode('utf8').replace(' ', '_')),)
        text = 'Matched based on the name. The page mentions %s.' % (join_albums(found_albums),)
        print ' * linking to %s' % (url,)
        print ' * edit note: %s' % (text,)
        mb.add_url("artist", a_gid, 179, url, text)
        break
    db.execute("INSERT INTO bot_wp_artist (gid) VALUES (%s)", (a_gid,))

