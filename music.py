import sys
import re
import json
import os

import dotenv
import requests
from flask import Flask, render_template, request
import yt_dlp
from ytmusicapi import YTMusic
from PIL import Image
import music_tag

dotenv.load_dotenv()

directory = os.getenv("OUTPUT_DIR", "./Music")
port = os.getenv("PORT", 5000)

ytmusic = YTMusic()
def get_albums(artist):
    if "browseId" in artist["albums"] and "params" in artist["albums"]:
        return ytmusic.get_artist_albums(artist["albums"]["browseId"], artist["albums"]["params"])
    else:
        return artist["albums"]["results"]
def get_singles(artist):
    if "browseId" in artist["singles"] and "params" in artist["singles"]:
        return ytmusic.get_artist_albums(artist["singles"]["browseId"], artist["singles"]["params"])
    else:
        return artist["singles"]["results"]

def remove_text_between_parens(text):
    n = 1  # run at least once
    while n:
        text, n = re.subn(r'\([^()]*\)', '', text)  # remove non-nested/flat balanced parts
    return text

def get_thumbnail_url(thumbnails):
    MAX_ALLOWED_PIXELS = 1000 * 1000 # we don't want higher resolution images since they will take a lot of space
    max_pixels = 0
    max_url = None
    for thumbnail in thumbnails:
        pixels = thumbnail["width"] * thumbnail["height"]
        if pixels > max_pixels and pixels < MAX_ALLOWED_PIXELS:
            max_pixels = pixels
            max_url = thumbnail["url"]
    return max_url

def download_image_url(url, filename):
    img = Image.open(requests.get(url, stream=True).raw)
    img.save(filename)

def get_yt_dlp_opts(output_template):
    return {
        'final_ext': 'm4a',
        'format': 'm4a/bestaudio/best',
        'writethumbnail': True,
        'outtmpl': {'default': output_template, 'pl_thumbnail': ''},
        # See help(yt_dlp.postprocessor) for a list of available Postprocessors and their arguments
        'postprocessors': [
            {  # Extract audio using ffmpeg
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
            },
            {
                'add_chapters': True,
                'add_infojson': 'if_exists',
                'add_metadata': True,
                'key': 'FFmpegMetadata',
            },
            {
                'already_have_thumbnail': False, 
                'key': 'EmbedThumbnail'
            },
        ],
        "sleep_interval": 2,
        "max_sleep_interval": 6,

        'fragment_retries': 10,
        'js_runtimes': {'deno': {'path': None}, 'node': {'path': None}},
        'remote_components': ['ejs:github'],
        'retries': 10,
        # 'skip_download': None,
        # 'warn_when_outdated': True,
    }
def get_singles_filter(exclude):
    def exclude_filter(info, *, incomplete):
        title = info.get('title')
        if title in exclude:
            return 'The music is excluded since it exists in an album'
        return None
    return exclude_filter

def download_artist(artist, directory = "./"):
    albums = get_albums(artist)
    urls = []
    for album in albums:
        playlist_id = ""
        if "audioPlaylistId" in album:
            playlist_id = album["audioPlaylistId"]
        else:
            playlist_id = album["playlistId"]
        urls.append(f"https://music.youtube.com/playlist?list={playlist_id}")

    songs = []
    song_albums = []
    for album in albums:
        album_data = ytmusic.get_album(album["browseId"])
        for song in album_data["tracks"]:
            songs.append(song["title"])
            song_albums.append(album_data["title"])
    singles = get_singles(artist)
    singles_urls = []
    # for album in singles:
    #     singles_urls.append(f"https://music.youtube.com/playlist?list={album["playlistId"]}")
        # print(album)
    # print(urls)
    # singles = ytmusic.get_album(artist["singles"]["browseId"])
    # print(singles["audioPlaylistId"])
    if directory.endswith("/"):
        directory = directory[:-1]
    
    exclude = set()
    for single in singles:
        singles_urls.append(f"https://music.youtube.com/playlist?list={single["playlistId"]}")
        album = ytmusic.get_album(single["browseId"])
        for track in album["tracks"]:
            if track["title"] in songs:
                index = songs.index(track["title"])
                album_name = song_albums[index]
                exclude.add(track["title"])


    ydl_opts = get_yt_dlp_opts(f'{directory}/{artist["name"]}/Singles/%(album,playlist)s/%(track,title)s.%(ext)s')
    ydl_opts["match_filter"] = get_singles_filter(exclude)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.download(singles_urls)
        # print(info)

    ydl_opts = get_yt_dlp_opts(f'{directory}/{artist["name"]}/%(album,playlist)s/%(track,title)s.%(ext)s')
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.download(urls)
        # print(info)

    # artist_thumbnail = get_thumbnail_url(artist["thumbnails"])
    # download_image_url(artist_thumbnail, f"{directory}/{artist["name"]}/folder.png")
    for album in albums:
        thumbnail = get_thumbnail_url(album["thumbnails"])
        download_image_url(thumbnail, f"{directory}/{artist["name"]}/{album["title"]}/cover.png")


def get_song_album_from_collection(song, albums):
    for album in albums:
        album = ytmusic.get_album(album["browseId"])
        for track in album["tracks"]:
            if track["title"] == song["videoDetails"]["title"]:
                return album
    return None

def get_song_directory(song):
    artist = ytmusic.get_artist(song["videoDetails"]["channelId"])
    albums = get_albums(artist)
    album = get_song_album_from_collection(song, albums)
    if album["title"] is not None:
        return f"{artist["name"]}/{album["title"]}", album

    singles = get_singles(artist)
    album = get_song_album_from_collection(song, singles)
    if album is not None:
        return f"{artist["name"]}/Singles/{album["title"]}", album
    return None, None
    

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

@app.route('/')
def home():
    query = request.args["q"]
    results = []
    if query is not None and query.strip() != "":
        results = ytmusic.search(query, None, None, 30)
    return render_template("template.html", results=results)

@app.route("/download", methods=["POST"])
def download():
    chosen = request.get_json()
    if chosen["resultType"] == "song":
        video_id = chosen["videoId"]
        song = ytmusic.get_song(video_id)
        song_path, album = get_song_directory(song)
        if song_path == None:
            return "Not a song", 400
        path = f"{directory}/{song_path}/{song["videoDetails"]["title"]}"
        ydl_opts = get_yt_dlp_opts(f'{path}.%(ext)s')
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.download([f"https://music.youtube.com/watch?v={video_id}"])
            file = music_tag.load_file(f"{path}.m4a")
            if f"{file["album"]}" == "":
                file["album"] = album["title"]
                file.save()
    elif chosen["resultType"] == "album":
        album = ytmusic.get_album(chosen["browseId"])
        print("album type:", album["type"])
        artist = album["artists"][0]["name"]
        path = f"{directory}/{artist}/%(album,playlist)s/%(track,title)s.%(ext)s"
        # ydl_opts = get_yt_dlp_opts(f'{directory}/{artist["name"]}/%(album,playlist)s/%(track,title)s.%(ext)s')
        ydl_opts = get_yt_dlp_opts(path)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            url = f"https://music.youtube.com/playlist?list={album["audioPlaylistId"]}"
            info = ydl.download([url])
        thumbnail = get_thumbnail_url(album["thumbnails"])
        download_image_url(thumbnail, f"{directory}/{artist}/{album["title"]}/cover.png")
    elif chosen["resultType"] == "artist":
        artist_browse_id = ""
        if chosen["category"] == "Top result":
            artist_browse_id = chosen["artists"][0]["id"]
        else:
            artist_browse_id = chosen["browseId"]
        artist = ytmusic.get_artist(artist_browse_id)
        download_artist(artist, directory)
    else:
        return "Not supported", 400
    return "Success", 200

if __name__ == '__main__':
    app.run(port=port)
