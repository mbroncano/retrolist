# retrolist
Python script that generates a 1G1R playlist for the default [Retroarch](http://www.libretro.com) frontend, using [DAT-o-MATIC](http://datomatic.no-intro.org) parent/clone XML DAT files.

## Description
This python script generates a Retroarch compatible [playlist](https://github.com/libretro/RetroArch/wiki/Manually-Creating-Custom-Playlists), similar to the ones generated automatically wit the feature 'Scan Directory', but filtering as many files as possible to get a single game per region.

A rom set usually contains a large number of version of the same game, be it corresponding to different [regions](https://en.wikipedia.org/wiki/GoodTools#Country_codes) or other [variants](https://en.wikipedia.org/wiki/GoodTools#Universal_codes) such as bootleg copies, hacks, etc.

The script used the information from a [DAT-o-MATIC](http://datomatic.no-intro.org/?page=download&fun=xml) parent/clone xml data file to, out of the available versions of a game in the romset:
 - Filter a particular set of regions when possible
 - Specify the priority of those regions (i.e. prefer USA over Europe versions)
 - Select the parent version over the clones (e.g version 2 over version 1)
 - Choose released version over unreleased (i.e. betas, prototypes)

After the playlist is generated, it creates a series of zip files for each of the selected files in the current directory, comprising the sanitized rom set.
 
The software has been written using Python 2.7 in OS X, and it depends on crcmod to compute the CRC32 for the rom files, although alternatively it could used MD5.

## Usage
The script accepts the following parameters
 - An XML file containing a parent/clone datafile
 - A prefix for the destination path
 - A series of directories containing the rom files

The region preferences is hard-coded to USA, EUR and JPN and can be changed inside the script.

## Example

 * Download a complete romset compiled with [GoodTools](https://en.wikipedia.org/wiki/GoodTools). For a lot of 8 and 16 bit systems, Emuparadise is a good place to start.
 * Download the parent/clone xml data file from [DAT-o-MATIC](http://datomatic.no-intro.org/?page=download&fun=xml). Remember to select the correct system on the top of the screen.
 * Extract all files to a single directory
```
$ mkdir roms
$ cd roms
$ for i in *; do unzip -j "$i"; done
```
 * Run the script
```
$ ./retrolist.py database.xml playlist.lpl "/storage/roms/" roms/
```
 * Copy the playlist and the sanitized rom set to the Retroarch directory

### version
1.0
