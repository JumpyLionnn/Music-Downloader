# Music Downloader
A simple music browser and downloader from youtube music

It serves a web page to download songs, albums and artists
It does not download music videos, episodes, podcasts and playlists

the output directory structure fits jellyfin's music library structure (artist/album/track.m4a)

It's a quick and dirty web ui and the server is not secure at all, use at your own risk
It is made using yt-dlp and youtube music web page api to browse and download tracks
The server will also attempt to download lyrics from lrc lib, although as I have observed, it's not that reliable
In addition, rsgain will try to adjust the gain of the song automatically
Song Genres will be searched for using musicbrainz api and embedded if found

100% Human made

## Usage
Install all of the dependencies
```
pip install -r requirements.txt
sudo apt install rsgain # or install for your environment https://github.com/complexlogic/rsgain#installation
```
then run using
```
python music.py
```

Here is an example of a config .env file
```env
PORT=5000
OUTPUT_DIR=./Music/
MAX_LYRICS_DURATION_DIFF=15 # In seconds
THUMBNAIL_SIZE=320 # The width and the height of each song thumbnail in pixels, higher means better quality (usually) smaller means smaller file sizes
RSGAIN_PATH=rsgain # The path to rsgain binary
```



## DISCLAIMER
This project is for educational purposes only :)
