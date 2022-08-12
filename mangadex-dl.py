#!/usr/bin/env python3

# Copyright (c) 2019-2021 eli fessler
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import requests, time, os, sys, re, json, html, zipfile, argparse, shutil

A_VERSION = "0.7"

def pad_filename(str):
    digits = re.compile('(\\d+)')
    pos = digits.search(str)
    if pos:
        return str[1:pos.start()] + pos.group(1).zfill(3) + str[pos.end():]
    else:
        return str

def float_conversion(tupl):
    try:
        x = float(tupl[0]) # (chap_num, chap_uuid)
    except ValueError: # empty string for oneshot
        x = 0
    return x

def find_id_in_url(url_parts):
    for part in url_parts:
        if "-" in part:
            return part

def zpad(num):
    if "." in num:
        parts = num.split('.')
        return "{}.{}".format(parts[0].zfill(3), parts[1])
    else:
        return num.zfill(3)

def get_uuid(manga_id):
    headers = {'Content-Type': 'application/json'}
    payload = '{"type": "manga", "ids": [' + str(manga_id) + ']}'
    try:
        r = requests.post("https://api.mangadex.org/legacy/mapping",
                headers=headers, data=payload)
    except:
        print("Error. Maybe the MangaDex API is down?")
        exit(1)
    try:
        resp = r.json()
        uuid = resp[0]["data"]["attributes"]["newId"]
    except:
        print("Please enter a valid MangaDex manga (not chapter) URL or ID.")
        exit(1)
    return uuid

def get_title(uuid, lang_code):
    r = requests.get("https://api.mangadex.org/manga/{}".format(uuid))
    resp = r.json()
    try:
        title = resp["data"]["attributes"]["title"][lang_code]
    except KeyError: # if no manga title in requested dl language
        try:
            # lookup in altTitles
            alt_titles = {}
            titles = resp["data"]["attributes"]["altTitles"]
            for val in titles:
                alt_titles.update(val)
            title = alt_titles[lang_code]
        except:
            # fallback to English title
            try:
                title = resp["data"]["attributes"]["title"]["en"]
            except:
                print("Error - could not retrieve manga title.")
                exit(1)
    return title

def uniquify(title, chapnum, groupname, basedir):
    counter = 1
    dest_folder = os.path.join(os.getcwd(), basedir, title, "{} [{}]".format(chapnum, groupname))
    while os.path.exists(dest_folder):
        dest_folder = os.path.join(os.getcwd(), basedir, title, "{}-{} [{}]".format(chapnum, counter, groupname))
        counter += 1
    return dest_folder

def dl(manga_id, lang_code, zip_up, ds, outdir, chapter_input=None):
    uuid = manga_id

    if manga_id.isnumeric():
        uuid = get_uuid(manga_id)

    title = get_title(uuid, lang_code)
    print("\nTITLE: {}".format(html.unescape(title)))

    # check available chapters & get images
    chap_list = []
    content_ratings = "contentRating[]=safe"\
            "&contentRating[]=suggestive"\
            "&contentRating[]=erotica"\
            "&contentRating[]=pornographic"
    r = requests.get("https://api.mangadex.org/manga/{}/feed"\
            "?limit=0&translatedLanguage[]={}&{}"
            .format(uuid, lang_code, content_ratings))
    try:
        total = r.json()["total"]
    except KeyError:
        print("Error retrieving the chapters list. "\
                "Did you specify a valid language code?")
        exit(1)

    if total == 0:
        print("No chapters available to download!")
        exit(0)

    offset = 0
    while offset < total: # if more than 500 chapters!
        r = requests.get("https://api.mangadex.org/manga/{}/feed"\
                "?order[chapter]=asc&order[volume]=asc&limit=500"\
                "&translatedLanguage[]={}&offset={}&{}"
                .format(uuid, lang_code, offset, content_ratings))
        chaps = r.json()
        chap_list += chaps["data"]
        offset += 500

    # chap_list is not empty at this point
    print("Available chapters:")
    print(" " + ', '.join(map(
        lambda x: "Oneshot" if x["attributes"]["chapter"] is None
        else x["attributes"]["chapter"],
        chap_list)))

    # i/o for chapters to download
    requested_chapters = []
    dl_list = chapter_input
    if dl_list is None:
        dl_list = input("\nEnter chapter(s) to download: ").strip()
    dl_list = [s.strip() for s in dl_list.split(',')]
    chap_list_only_nums = [i["attributes"]["chapter"] for i in chap_list]
    for s in dl_list:
        if "-" in s: # range
            split = s.split('-')
            lower_bound = split[0]
            upper_bound = split[-1]
            try:
                lower_bound_i = chap_list_only_nums.index(lower_bound)
            except ValueError:
                print("Chapter {} does not exist. Skipping range {}."
                    .format(lower_bound, s))
                continue # go to next iteration of loop
            upper_bound_i = len(chap_list) - 1
            if upper_bound != 'end':
                try:
                    upper_bound_i = chap_list_only_nums.index(upper_bound)
                except ValueError:
                    print("Chapter {} does not exist. Skipping range {}."
                        .format(upper_bound, s))
                    continue
            s = chap_list[lower_bound_i:upper_bound_i+1]
        elif s.lower() == "oneshot":
            if None in chap_list_only_nums:
                oneshot_idxs = [i
                        for i, x in enumerate(chap_list_only_nums)
                        if x is None]
                s = []
                for idx in oneshot_idxs:
                    s.append(chap_list[idx])
            else:
                print("Chapter Oneshot does not exist. Skipping.")
                continue
        elif s.lower() == "":
            requested_chapters.extend(chap_list)
            break
        else: # single number (but might be multiple chapters numbered this)
            chap_idxs = [i for i, x in enumerate(chap_list_only_nums) if x == s]
            if len(chap_idxs) == 0:
                print("Chapter {} does not exist. Skipping.".format(s))
                continue
            s = []
            for idx in chap_idxs:
                s.append(chap_list[idx])
        requested_chapters.extend(s)

    # get chapter json(s)
    print()
    progress_indicator = ["|", "/", "–", "\\"]
    for index, chapter in enumerate(requested_chapters):
        print("Downloading chapter {} [{}/{}]".format(
            chapter["attributes"]["chapter"]
            if chapter["attributes"]["chapter"] is not None
            else "Oneshot", index+1, len(requested_chapters)))

        r = requests.get("https://api.mangadex.org/at-home/server/{}"
                .format(chapter["id"]))
        chapter_data = r.json()
        baseurl = chapter_data["baseUrl"]

        # make url list
        images = []
        accesstoken = ""
        chaphash = chapter_data["chapter"]["hash"]
        datamode = "dataSaver" if ds else "data"
        datamode2 = "data-saver" if ds else "data"
        errored = False

        for page_filename in chapter_data["chapter"][datamode]:
            images.append("{}/{}/{}/{}".format(
                baseurl, datamode2, chaphash, page_filename))

        # get group names & make combined name
        group_uuids = []
        for entry in chapter["relationships"]:
            if entry["type"] == "scanlation_group":
                group_uuids.append(entry["id"])

        groups = ""
        for i, group in enumerate(group_uuids):
            if i > 0:
                groups += " & "
            r = requests.get("https://api.mangadex.org/group/{}".format(group))
            name = r.json()["data"]["attributes"]["name"]
            groups += name
        groupname = re.sub('[/<>:"/\\|?*]', '-', groups)

        title = re.sub('[/<>:"/\\|?*]', '-', html.unescape(title))
        if (chapter["attributes"]["chapter"]) is None:
            chapnum = "Oneshot"
        else:
            chapnum = 'c' + zpad(chapter["attributes"]["chapter"])

        dest_folder = uniquify(title, chapnum, groupname, outdir)
        if not os.path.exists(dest_folder):
            os.makedirs(dest_folder)

        # download images
        for pagenum, url in enumerate(images, 1):
            filename = os.path.basename(url)
            ext = os.path.splitext(filename)[1]

            dest_filename = pad_filename("{}{}".format(pagenum, ext))
            outfile = os.path.join(dest_folder, dest_filename)

            r = requests.get(url)
            # go back to the beginning and erase the line before printing more
            print("\r\033[K{} Downloading pages [{}/{}]".format(
                progress_indicator[(pagenum-1)%4], pagenum, len(images)),
                end='', flush=True)
            if r.status_code == 200:
                with open(outfile, 'wb') as f:
                    f.write(r.content)
            else:
                # silently try again
                time.sleep(2)
                r = requests.get(url)
                if r.status_code == 200:
                    errored = False
                    with open(outfile, 'wb') as f:
                        f.write(r.content)
                else:
                    errored = True
                    print("\n Skipping download of page {} - error {}.".format(
                        pagenum, r.status_code))
                    raise Exception(chapter["attributes"]["chapter"] if chapter["attributes"]["chapter"] is not None else "oneshot")
            time.sleep(0.2) # within limit of 5 requests per second
            # not reporting https://api.mangadex.network/report telemetry for now, sorry

        if zip_up:
            zip_name = os.path.join(os.getcwd(), outdir, title,
                    "{} {} [{}].cbz".format(title, chapnum, groupname))
            chap_folder = os.path.join(os.getcwd(), outdir, title,
                    "{} [{}]".format(chapnum, groupname))
            with zipfile.ZipFile(zip_name, 'w') as myzip:
                for root, dirs, files in os.walk(chap_folder):
                    for file in files:
                        path = os.path.join(root, file)
                        myzip.write(path, os.path.basename(path))
            shutil.rmtree(chap_folder) # remove original folder of loose images
        if not errored:
            if len(requested_chapters) != index+1:
                # go back to chapter line and clear it and everything under it
                print("\033[F\033[J", end='', flush=True) 
            else:
                print("\r\033[K", end='', flush=True)
    print("Done.")

def dlWrapper(url_input, lang, cbz, datasaver, outdir, chapter_input=None):
    url_parts = url_input.split('/')
    manga_id = find_id_in_url(url_parts)
    print(manga_id)
    dl(manga_id, lang, cbz, datasaver, outdir, chapter_input)

def popline(filepath):
    with open(filepath, "r+") as f:
        line = f.readline().strip()
        data = f.read()
        f.seek(0)
        f.write(data)
        f.truncate()
        return line

def appendline(filepath, line):
    with open(filepath, "a") as f:
        f.write(f'\n{line}')

def EOF(filepath):
    with open(filepath, "r") as f:
        current_pos = f.tell()
        file_size = os.fstat(f.fileno()).st_size
        return current_pos >= file_size

if __name__ == "__main__":
    print("mangadex-dl v{}".format(A_VERSION))

    parser = argparse.ArgumentParser()
    parser.add_argument("-l", dest="lang", required=False,
            action="store", default="en",
            help="download in specified language code (default: en)")
    parser.add_argument("-d", dest="datasaver", required=False,
            action="store_true",
            help="download images in lower quality")
    parser.add_argument("-a", dest="cbz", required=False,
            action="store_true",
            help="package chapters into .cbz format")
    parser.add_argument("-o", dest="outdir", required=False,
            action="store", default="download",
            help="specify name of output directory")
    parser.add_argument("-f", dest="file", nargs='?', required=False,
            help="specify path of input file")
    args = parser.parse_args()

    lang_code = "en" if args.lang is None else str(args.lang)

    if '-f' in sys.argv:
        input_file = "./input.txt" if args.file is None else str(args.file)
        if not os.path.exists(input_file):
            with open(input_file, "w+"): pass
        while not EOF(input_file):
            line = popline(input_file)
            if line == "": continue
            try:
                fragments = line.split("|")
                url = fragments[0]
                chapter_input = ''
                if len(fragments) > 1:
                    chapter_input = fragments[-1]
                dlWrapper(url, lang_code, args.cbz, args.datasaver, args.outdir, chapter_input)
            except Exception as e:
                print("Error!")
                # try again, begin at failed chapter
                time.sleep(2)
                if float(str(e)): 
                    appendline(input_file, f'{url}|{e}-end')
                else:
                    appendline(input_file, f'{url}|{str(e)}')
    else:
        urlInput = "";
        # prompt for manga
        while urlInput == "":
            urlInput = input("Enter manga URL or ID: ").strip()
        urlInput = urlInput.split(',')
        urls = [item.strip() for item in urlInput]
        for item in urls:
            try:
                dlWrapper(item, lang_code, args.cbz, args.datasaver, args.outdir, '' if len(urls) > 1 else None)
            except:
                print("Error with URL. Skipping.")
                continue
