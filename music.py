import sys
import re
import json
import os
import itertools
import time
from pathlib import Path
import collections
import subprocess
import threading

import dotenv
import requests
from flask import Flask, render_template, request
import yt_dlp
from ytmusicapi import YTMusic
from PIL import Image
import music_tag
from lrclib import LrclibClient
import musicbrainzngs



musicbrainzngs.set_useragent("music_downloader", "0.1", "fake_mail@example.com")
all_genres = set()

class GenreCollector(threading.Thread): 
    def __init__(self): 
        threading.Thread.__init__(self) 

        # helper function to execute the threads
    def run(self): 
        offset = 0
        genre_count = 1
        while offset < genre_count:

            page = requests.get(f"https://musicbrainz.org/ws/2/genre/all?limit=100&offset={offset}&fmt=json").json()
            while "error" in page:
                time.sleep(1)
                page = requests.get(f"https://musicbrainz.org/ws/2/genre/all?limit=100&offset={offset}&fmt=json").json()

            genre_count = page["genre-count"]
            print(f"Fetching genres {len(all_genres)}/{genre_count}")
            offset += len(page["genres"])
            for genre in page["genres"]:
                all_genres.add(genre["name"])
            time.sleep(1)
        print("Finsihed genres fetching")

        
collector = GenreCollector()
collector.start()

class MusicBrainzRelease:
    def __init__(self, release):
        self.release = release

    @staticmethod
    def find_release(artist_name, album_name, track_count):
        result = musicbrainzngs.search_releases(artist=artist_name, release=album_name, limit=20)
        if not result['release-list']:
            print("Music brainz album not found")
            return None

        # Loop through the search results to find an exact match
        for release in result['release-list']:
            exact_artist = any(isinstance(artist, dict) and artist['name'].lower() == artist_name.lower() for artist in release['artist-credit'])
            if release['title'].lower() == album_name.lower() and exact_artist:
                release_id = release['id']
                result = musicbrainzngs.get_release_by_id(release_id, includes=['recordings'])
                tracks = result['release']['medium-list'][0]['track-list']
                if track_count == len(tracks):
                    return MusicBrainzRelease(result["release"])
        # If no exact match is found, return a message indicating this
        print(f"MusicBrainz: Exact album not found {album_name}.")
        return None

    def get_track_genres(self, track_name):
        track_name = track_name.lower()
        tracks = self.release['medium-list'][0]['track-list']
        for track in tracks:
            track = track["recording"]
            if track["title"].lower() == track_name:
                recording = musicbrainzngs.get_recording_by_id(track["id"], includes=["tags"])
                recording = recording["recording"]
                if "tag-list" not in recording:
                    return []
                tags = recording["tag-list"]
                track_genres = []
                for tag in tags:
                    if tag["name"] in all_genres:
                        track_genres.append(tag["name"])
                print(track_genres)
                return track_genres
                #return recording

        return []



dotenv.load_dotenv()

directory = os.getenv("OUTPUT_DIR", "./Music")
port = os.getenv("PORT", 5000)
lyrics_duration_diff = os.getenv("MAX_LYRICS_DURATION_DIFF", 15)
thumbnail_size = os.getenv("THUMBNAIL_SIZE", 320)
rsgain_path = os.getenv("RSGAIN_PATH", "rsgain")

ytmusic = YTMusic()


mp4_tag_mapping = {
    "releasetype": "----:com.apple.iTunes:MusicBrainz Album Type",
}
class AutoGainTagPP(yt_dlp.postprocessor.PostProcessor):
    def run(self, info):
        file_path = info["filepath"]
        args = [rsgain_path, "custom", "--output", "--tagmode=i", file_path];
        result = subprocess.run(args, capture_output=True, text=True) 
        if result.returncode != 0:
            self.to_screen("Failed to find gain to track")
            self.to_screen(result.stdout)
        else:
            self.to_screen(result.stdout[result.stdout.find("\n")+1:])
        return [], info
class LyricsDownloaderPP(yt_dlp.postprocessor.PostProcessor):
    client = LrclibClient("Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0")
    def __init__(self, ytmusic = None, song = None, downloader = None):
        super().__init__(downloader)
        self.ytmusic = ytmusic
        self.song = song

    def get_lrclib_lyrics(self, info):
        artists = info.get("artists") if self.song is None else self.song["artists"]
        title = info.get("title") if self.song is None else self.song["title"]

        # path = Path(info["requested_downloads"][0]["filename"])
        duration = int(info["duration"])
        # time.sleep(1)
        results = []
        try:
            results = self.client.search(title, artists[0])
        except:
            self.to_screen("Encountered an error while fetching lyrics, trying again...")
            time.sleep(8)
            try:
                results = self.client.search(title, artists[0])
            except:
                self.to_screen("Encountered an error again while fetching lyrics, skipping...") 
                return None

        
        best_res = None
        for result in results:
            if not result.instrumental:
                if result.status == "Synced" and result.duration is not None:
                    duration_diff = abs(result.duration - duration)
                    if duration_diff <= lyrics_duration_diff:
                        if best_res is None or duration_diff < best_res["duration_diff"]:
                            best_res = {
                                "id": result.song_id,
                                "lyrics": result.synced_lyrics,
                                "duration_diff": duration_diff,
                            }
        return best_res
    
    @staticmethod
    def yt_lyric_time_label(time):
        minutes = time / (1000 * 60)
        seconds = time / 1000 % 60
        miliseconds = time % 1000
        return f"{minutes:02d}:{seconds:02d}.{miliconds / 10:02d}"

    @staticmethod
    def yt_lyrics_to_lrc(lyrics):
        text = ""
        prev_line_end_time = 0
        for line in lyrics:
            if prev_line_end_time - line.start_time > 2000:
                time = LyricsDownloaderPP.yt_lyric_time_label(prev_line_end_time)
                text += f"[{time}] \n"
            start_time = LyricsDownloaderPP.yt_lyric_time_label(line.start_time)
            text += f"[{start_time}] {line.text}\n"
            prev_line_end_time = line.end_time
        return text

    def get_yt_music_lyrics(self, video_id):
        if self.ytmusic is not None:
            playlist = self.ytmusic.get_watch_playlist(video_id)
            lyrics_id = playlist["lyrics"]
            if lyrics_id is not None:
                lyrics = self.ytmusic.get_lyrics(lyrics_id)
                if lyrics is not None and lyrics["hasTimestamps"] == True:
                    return LyricsDownloaderPP.yt_lyrics_to_lrc(lyrics["lyrics"])
        return None
            
    def run(self, info):
        video_id = info["id"]
        
        lyrics = self.get_yt_music_lyrics(video_id)
        if lyrics is not None:
            self.to_screen("Writing lrc file from yt music lyrics")
        else:
            best_res = self.get_lrclib_lyrics(info)
            if best_res is not None:
                lyrics = best_res["lyrics"]
                self.to_screen(f"Writing lrc file with duration difference of {best_res["duration_diff"]}secs");

            
        if lyrics is None:
            self.to_screen(f"Coundn't find suitable lyrics for this song.");
            return [], info

        # track_path = Path(info["requested_downloads"][0]["filename"])
        track_path = Path(info["filepath"])
        path = f"{track_path.parent}/{track_path.stem}.lrc"
        info["lyrics"] = path
        self.to_screen(f"Downloaded lyrics to {path}.");
        with open(path, "w") as lrc_file:
            lrc_file.write(lyrics)
        return [], info

class MusicMetadataPP(yt_dlp.FFmpegPostProcessor):

    def __init__(self, ytmusic, data, downloader = None):
        yt_dlp.FFmpegPostProcessor.__init__(self, downloader)
        self.ytmusic = ytmusic
        self.data = data

    @staticmethod
    def _options(target_ext):
        audio_only = target_ext == 'm4a'
        yield from yt_dlp.FFmpegPostProcessor.stream_copy_opts(not audio_only)
        if audio_only:
            yield from ('-vn', '-acodec', 'copy')
    
    def get_yt_music_metadata(self, video_id, title):
        
        genres = []
        if "mb_release" in self.data:
            genres = self.data["mb_release"].get_track_genres(self.data["title"])
        # song_data = self.songs_data[video_id] if video_id in self.songs_data else self.songs
        song = self.ytmusic.get_song(video_id)
        # if "credits_browse_id" in song_data:
        #     song_credits = self.ytmusic.get_song_credits(song_data["credits_browse_id"])
        # playlist = ytmusic.get_watch_playlist(track["videoId"])
        return {
            "title": self.data["title"],
            "album": self.data["album"],
            "artist": ",".join(self.data["artists"]),
            "artists": self.data["artists"],
            "albumartist": ",".join(self.data["album_artists"]),
            "albumartists": self.data["album_artists"],
            "totaltracks": self.data["total_tracks"],
            "tracknumber": self.data["track_number"],
            "genre": ",".join(genres),
            # "releasedate": "T".split(song["microformat"]["microformatDataRenderer"]["publishDate"])[0],
            # "year": song["microformat"]["microformatDataRenderer"]["publishDate"].split("-")[0],
            # "producers": song_credits["produced_by"]["data"],
            "releasetype": self.data["release_type"],
        }

    def get_ytdlp_tags(self, info):
        tags = {}
        if "track" in info:
            tags["title"] = info.get("track")
        elif "title" in info:
            tags["title"] = info.get("title")

        if "track_number" in info:
            tags["tracknumber"] = info.get("track_number")

        if "artist" in info and info.get("artist") is not None:
            tags["artist"] = info.get("artist")
        elif "artists" in info and info.get("artists") is not None:
            tags["artist"] = ",".join(info.get("artists"))
            tags["artists"] = info.get("artists")
        elif "creator" in info and info.get("creator") is not None:
            tags["artist"] = info.get("creator")
        elif "creators" in info and info.get("creators") is not None:
            tags["artist"] = ",".join(info.get("creators"))
            tags["artists"] = info.get("creators")

        if "genre" in info and info.get("genre") is not None:
            tags["genre"] = info.get("genre")
        if "genres" in info and info.get("genres") is not None:
            tags["genre"] = ",".join(info.get("genres"))
        return tags



    @yt_dlp.postprocessor.PostProcessor._restrict_to(images=False)
    def run(self, info):
        if info["ext"] not in ("m4a", "mp3", "ogg"):
            self.to_screen("Unsupported file type for music metadata emdedding")
            return [], info
        filename = info['filepath']
        
        ytdlp_tags = self.get_ytdlp_tags(info)
        tags = ytdlp_tags | self.get_yt_music_metadata(info["id"], info["title"])
        lyrics_file_path = None
        if "lyrics" in info:
            lyrics_file_path = info["lyrics"]
            with open(lyrics_file_path, "r") as lrc_file:
                lyrics = lrc_file.read()
                tags["lyrics"] = lyrics
    
        file = music_tag.load_file(filename)
        for key, value in tags.items():
            try:
                file.set(key, value)
            except:
                if info["ext"] == "m4a" and key in mp4_tag_mapping:
                    tag = mp4_tag_mapping[key];
                    value = music_tag.mutagen.mp4.MP4FreeForm(value.encode("utf-8"))
                    file.mfile.tags[tag] = value
        file.save()
        self.to_screen("Downloaded Metadata")
        if lyrics_file_path is not None:
            self._delete_downloaded_files(lyrics_file_path, info=info)
            del info["lyrics"]

        return [], info

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

def format_date(date):
    try:
        num = int(date)
        year = date / 10000
        month = (date / 100) % 100
        day = date % 100
        return f"{year:04}-{month:02}-{day:02}"
    except:
        components = date.split("/")
        if len(components) != 3:
            return "-".join(components)
        components = date.split(".")
        if len(components) != 3:
            return "-".join(components)
        return components

def is_song_in_directory(parent_directory, song_name):
    for root, dirs, files in os.walk(parent_directory):
        if "Singles" not in root:
            for file in files:
                if song_name.lower() in file.lower():
                    return True
    return False

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
        'ignoreerrors': True,
        'writethumbnail': True,
        'outtmpl': {'default': output_template, 'pl_thumbnail': ''},
        # See help(yt_dlp.postprocessor) for a list of available Postprocessors and their arguments
        'postprocessors': [
            {  # Extract audio using ffmpeg
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
            },
        ],
        'postprocessor_args': {
            # crops the thumbnail to a square shape and resizes it to a fixed size
            'thumbnailsconvertor+ffmpeg_o': [
                '-c:v',
                'png',
                '-qmin',
                '1',
                '-qscale:v',
                '1',
                '-vf',
                f'scale={thumbnail_size}:{thumbnail_size}:force_original_aspect_ratio=increase,crop={thumbnail_size}:{thumbnail_size}'
            ]
        },
        "sleep_interval": 2,
        "max_sleep_interval": 6,

        'fragment_retries': 10,
        'js_runtimes': {'deno': {'path': None}, 'node': {'path': None}},
        'remote_components': ['ejs:github'],
        'retries': 10,
        # 'skip_download': None,
        # 'warn_when_outdated': True,
    }

def yt_dlp_download(urls, opts_overrides, songs):
    if "match_filter" in opts_overrides:
        opts_overrides["match_filter"] = match_filter_and(opts_overrides["match_filter"], is_duplicate_track_filter)
    else:
        opts_overrides["match_filter"] = is_duplicate_track_filter
    #for video_id in list(songs.keys()):
    #    title = songs[video_id]["title"]
    #    songs[title] = songs[video_id]
    output_template = opts_overrides.pop("output", "%(title)s.%(ext)s")
    for song in songs:
        output = output_template.replace("%(album)s", song["album"]).replace("%(title)s", song["title"])
        ydl_opts = get_yt_dlp_opts(output) | opts_overrides
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.add_post_processor(LyricsDownloaderPP(ytmusic, song))
            ydl.add_post_processor(MusicMetadataPP(ytmusic, song))
            ydl.add_post_processor(AutoGainTagPP())
            ydl.add_post_processor(yt_dlp.FFmpegThumbnailsConvertorPP(None, "png"))
            ydl.add_post_processor(yt_dlp.postprocessor.EmbedThumbnailPP())
            info = ydl.download([song["url"]])

# def get_singles_filter(exclude):
#     def exclude_filter(info, *, incomplete):
#         title = info.get('title')
#         if title in exclude:
#             return 'The music is excluded since it exists in an album'
#         return None
#     return exclude_filter

def is_duplicate_track_filter(info, *, incomplete=True):
    title = info.get("title")
    artists = info.get("artists")
    if artists is None or len(artists) == 1:
        return None
    for artist in artists:
        parent_directory = f"{directory}/{artist}"
        if os.path.isdir(parent_directory) and is_song_in_directory(parent_directory, title):
            print(f"skipping_track {title} - {info.get("filepath")}")
            return "The track is already downloaded, skipping"
    return None

def match_filter_and(filter_a, filter_b):
    def parent_filter(info, *args, incomplete = True):
        a = filter_a(info, *args, incomplete=incomplete)
        if a is None:
            return filter_b(info, *args, incomplete=incomplete)
        return a
    return parent_filter

def get_album_songs_data(album, release_type = "album"):
    songs_data = []
    album_artists = get_artists_names(album["artists"])
    album_title = album["title"]
    mb_release = MusicBrainzRelease.find_release(album_artists[0], album_title, len(album["tracks"]))
    for track in album["tracks"]:
        data = {
            "url": f"https://music.youtube.com/watch?v={track["videoId"]}",
            "title": track["title"],
            "album_artists": album_artists,
            "album": album_title,
            "total_tracks": album["trackCount"],
            "artists": get_artists_names(track["artists"]),
            "track_number": track["trackNumber"],
            # "credits_browse_id": track["creditsBrowseId"],
            "release_type": release_type,
            "genres": []
        }  
        if "creditsBrowseId" in track:
            data["credits_browse_id"] = track["creditsBrowseId"]
        if mb_release is not None:
            data["mb_release"] = mb_release
        songs_data.append(data)
            
    return songs_data

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

    songs_names = []
    album_songs = []
    # song_albums = []
    for album in albums:
        album_data = ytmusic.get_album(album["browseId"])
        album_songs.extend(get_album_songs_data(album_data))
        for song in album_data["tracks"]:
            songs_names.append(song["title"])
            # song_albums.append(album_data["title"])
    singles = get_singles(artist)
    singles_urls = []
    if directory.endswith("/"):
        directory = directory[:-1]
    
    #exclude = set()
    single_songs = []
    for single in singles:
        singles_urls.append(f"https://music.youtube.com/playlist?list={single["playlistId"]}")
        album = ytmusic.get_album(single["browseId"])
        songs = get_album_songs_data(album, "single")
        songs = [track for track in songs if track["title"] not in songs_names]
        single_songs.extend(songs)
        #for track in album["tracks"]:
        #  if track["title"] in songs_names:
                # index = songs.index(track["title"])
                # album_name = song_albums[index]
     #           exclude.add(track["title"])

    ydl_opts = {
        "output": f"{directory}/{artist["name"]}/%(album)s/%(title)s.%(ext)s"
    }
    yt_dlp_download(urls, ydl_opts, album_songs)

    ydl_opts = {
        "output": f'{directory}/{artist["name"]}/Singles/%(album)s/%(title)s.%(ext)s',
        # "match_filter": get_singles_filter(exclude)
    }
    yt_dlp_download(singles_urls, ydl_opts, single_songs)

    # artist_thumbnail = get_thumbnail_url(artist["thumbnails"])
    # download_image_url(artist_thumbnail, f"{directory}/{artist["name"]}/folder.png")
    for album in albums:
        album_dir = f"{directory}/{artist["name"]}/{album["title"]}"
        if os.path.isdir(album_dir):
            thumbnail = get_thumbnail_url(album["thumbnails"])
            download_image_url(thumbnail, f"{album_dir}/cover.png")


def get_song_album_from_collection(song, albums):
    for album in albums:
        album = ytmusic.get_album(album["browseId"])
        for track_index, track in enumerate(album["tracks"]):
            if track["title"] == song["videoDetails"]["title"]:
                return album, track_index
    return None, None

def get_song_directory(song):
    artist = ytmusic.get_artist(song["videoDetails"]["channelId"])
    albums = get_albums(artist)
    album, track_index = get_song_album_from_collection(song, albums)
    if album is not None:
        return f"{artist["name"]}/{album["title"]}", album, track_index

    singles = get_singles(artist)
    album, track_index = get_song_album_from_collection(song, singles)
    if album is not None:
        return f"{artist["name"]}/Singles/{album["title"]}", album, track_index
    return None, None

def get_artists_names(artists):
    names = []
    for artist in artists:
        names.append(artist["name"])
    return names
    

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

@app.route('/')
def home():
    results = []
    if "q" in request.args:
        query = request.args["q"]
        if query.strip() != "":
            results = ytmusic.search(query, None, None, 30)
    return render_template("template.html", results=results), 200

@app.route("/download", methods=["POST"])
def download():
    chosen = request.get_json()
    if chosen["resultType"] == "song":
        video_id = chosen["videoId"]
        song = ytmusic.get_song(video_id)
        song_path, album, track_index = get_song_directory(song)
        if song_path == None:
            return "Not a song", 400
        album_artists = get_artists_names(album["artists"])
        songs_data = [
            {
                "url": f"https://music.youtube.com/watch?v={video_id}",
                "title": song["videoDetails"]["title"],
                "album": album["title"],
                "artists": get_artists_names(album["tracks"][track_index]["artists"]),
                "track_number": album["tracks"][track_index]["trackNumber"],
                "album_artists": album_artists,
                "total_tracks": album["trackCount"],
                # "credits_browse_id": album["tracks"][track_index]["creditsBrowseId"],
                "release_type": album["type"].lower(),
            },
        ]
        if "creditsBrowseId" in album["tracks"][track_index]:
            songs_data[0]["credits_browse_id"] = album["tracks"][track_index]["creditsBrowseId"]
        mb_release = MusicBrainzRelease.find_release(album_artists[0], album_title, len(album["tracks"]))
        if mb_release is not None:
            songs_data[0]["mb_release"] = mb_release
        path = f"{directory}/{song_path}/{song["videoDetails"]["title"]}"
        ydl_opts = {
            "output": f'{path}.%(ext)s'
        }
        yt_dlp_download([], ydl_opts, songs_data)
    elif chosen["resultType"] == "album":
        album = ytmusic.get_album(chosen["browseId"])
        artist = album["artists"][0]["name"]
        output_insert = ""
        if album["type"] == "Single":
            output_insert = "/Singles"
        path = f"{directory}/{artist}{output_insert}"
        ydl_opts = {
            "output": f"{path}/%(album)s/%(title)s.%(ext)s",
        }
        songs_data = get_album_songs_data(album)
        url = f"https://music.youtube.com/playlist?list={album["audioPlaylistId"]}"
        yt_dlp_download([url], ydl_opts, songs_data)
        thumbnail = get_thumbnail_url(album["thumbnails"])
        download_image_url(thumbnail, f"{path}/{album["title"]}/cover.png")
    elif chosen["resultType"] == "artist":
        artist_browse_id = ""
        if "category" in chosen and chosen["category"] == "Top result":
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
