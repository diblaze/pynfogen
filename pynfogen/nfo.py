import glob
import os
import re
import html
import textwrap
import json
from typing import List, Union, Tuple

import pycountry
from pvsfunc.helpers import anti_file_prefix, get_d2v
from pyd2v import D2V
from pymediainfo import MediaInfo

from pynfogen.formatter import CustomFormats
from pynfogen.helpers import scrape


class NFO:

    AUDIO_CHANNEL_LAYOUT_WEIGHT = {
        "LFE": 0.1
    }

    def __init__(self):
        self.media_info = None
        self.file = None
        self.season = None
        self.episode = None
        self.episode_name = None
        self.imdb = None
        self.tmdb = None
        self.tvdb = None
        self.title_name = None
        self.title_year = None
        self.episodes = None
        self.release_name = None
        self.preview_url = None
        self.preview_images = []
        self.banner_image = None
        self.source = None
        self.note = None
        self.videos = None
        self.audio = None
        self.subtitles = None
        self.chapters = None
        self.chapters_numbered = None

        self.fanart_api_key = None

    def __repr__(self):
        return "<{c} {attrs}>".format(
            c=self.__class__.__name__,
            attrs=" ".join("{}={!r}".format(k, v) for k, v in self.__dict__.items()),
        )

    def run(self, template: str, art: str = None, **kwargs) -> str:
        """
        Evaluate and apply formatting on template, apply any art if provided.
        Any additional parameters are passed as extra variables to the template.
        The extra variables have priority when there's conflicting variable names.
        """
        variables = self.__dict__
        variables.update(kwargs)

        template = CustomFormats().format(template, **variables)
        if art:
            art = art.format(nfo=template)
            template = art

        for m in re.finditer(r"<\?([01])\?([\D\d]*?)\?>", template):
            # TODO: This if check is quite yucky, look into alternative options.
            #       Ideally a custom format spec would be great.
            template = template.replace(
                m.group(0),
                m.group(2) if int(m.group(1)) else ""
            )

        template = "\n".join(map(str.rstrip, template.splitlines(keepends=False)))

        return template

    def set_config(self, file: str, season: Union[int, str] = None, episode: Tuple[int, str] = None, **config):
        if not config or not isinstance(config, dict):
            raise ValueError("NFO.set_config: Parameter config is empty or not a dictionary...")

        self.file = anti_file_prefix(file)
        self.season = season
        self.episode, self.episode_name = episode

        self.media_info = MediaInfo.parse(self.file)
        self.videos = self.media_info.video_tracks
        self.audio = self.media_info.audio_tracks
        self.subtitles = self.media_info.text_tracks
        self.chapters = self.media_info.menu_tracks
        self.chapters = None if not self.chapters else [v for k, v in self.chapters[0].to_data().items() if ("1" + k.replace("_", "")).isdigit()]
        self.chapters_numbered = 0 if not self.chapters else sum(
            1 for i, x in enumerate(self.chapters) if x.split(":", 1)[-1] in [f"Chapter {i+1}", f"Chapter {str(i+1).zfill(2)}"]
        ) == len(self.chapters)

        self.fanart_api_key = config.get("fanart_api_key")
        self.imdb, self.tmdb, self.tvdb = self.get_database_ids(config)

        self.title_name, self.title_year = self.get_title_name_year()

        self.episodes = self.get_tv_episodes()

        self.release_name = self.get_release_name()

        self.preview_url = config["preview-url"]
        self.preview_images = self.get_preview_images(self.preview_url)
        self.banner_image = self.get_banner_image(self.tvdb) if self.tvdb else None

        self.source = config["source"]
        self.note = config["note"]

        print(self)

    def get_database_ids(self, config):
        general = self.media_info.general_tracks[0].to_data()
        dbs = {"imdb": None, "tmdb": None, "tvdb": None}
        for db in dbs.keys():
            if db in general and general[db]:
                dbs[db] = general[db]
            elif db in config and config[db]:
                dbs[db] = config[db]
        if not dbs["imdb"]:
            while not dbs["imdb"]:
                dbs["imdb"] = input("An IMDB ID is required but wasn't found, what's it's IMDb ID?\n")
            dbs["tmdb"] = input("While we're at it, does this title have a TMDB ID?\n") or None
            dbs["tvdb"] = input("What about a TVDB ID?\n") or None
        for k, v in dbs.items():
            if not v:
                print(f"Warning: No {k} ID was found...")
        return dbs.values()

    def get_title_name_year(self) -> tuple:
        imdb_page = html.unescape(scrape(f"https://www.imdb.com/title/{self.imdb}"))
        imdb_title = re.search(
            # testing ground: https://regex101.com/r/dRpT6g/3
            r"<title>(?P<name>.+) \(((?P<type>TV (Movie|Series|Mini-Series|Short|Episode) |Video |)"
            r"(?P<year>(\d{4})(|– |–\d{4})))\) - IMDb</title>",
            imdb_page
        )
        if not imdb_title:
            raise ValueError(f"Could not scrape Movie Title or Year for {self.imdb}...")
        return imdb_title.group("name").strip(), imdb_title.group("year").strip()

    def get_tv_episodes(self) -> int:
        """Calculate total episode count based on neighbouring same-extension files."""
        return len(glob.glob(os.path.join(
            os.path.dirname(self.file),
            f"*{os.path.splitext(self.file)[-1]}"
        )))

    def get_release_name(self) -> str:
        # Retrieve the release name based on the input file or parent folder
        if self.season is not None and self.episode is None:
            return os.path.basename(os.path.dirname(self.file))
        return os.path.splitext(os.path.basename(self.file))[0]

    def get_banner_image(self, tvdb_id: int):
        if not tvdb_id:
            return None
        if not self.fanart_api_key:
            raise ValueError("Need Fanart.tv api key for TV titles!")
        res = scrape(f"http://webservice.fanart.tv/v3/tv/{tvdb_id}?api_key={self.fanart_api_key}")
        res = json.loads(res)
        if "error message" in res:
            if res["error message"] == "Not found":
                return None
            raise ValueError(f"Fanart.tv spat out an error! {res}")
        if "tvbanner" not in res or not any(x for x in res["tvbanner"] if x["lang"] == "en"):
            return None
        return res["tvbanner"][0]["url"]

    def get_video_print(self, videos) -> list:
        if not videos:
            return ["--"]
        data = []
        for t in videos:
            codec = {
                "MPEG Video": f"MPEG-{(t.format_version or '').replace('Version ', '')}"
            }.get(t.format, t.format)
            interlaced_percent = None
            vst = False
            if codec in ["MPEG-1", "MPEG-2"]:
                # make sure a d2v file for this video exists
                self.file = get_d2v(self.file)
                # parse d2v file with pyd2v
                d2v = D2V(self.file)
                # get every frames' flag data, this contains information on displaying frames
                # add vob and cell number to each frames flag data as well
                flags = [f for line in [
                    [dict(**y, vob=x["vob"], cell=x["cell"]) for y in x["flags"]] for x in d2v.data
                ] for f in line]
                interlaced_percent = (sum(1 for f in flags if not f["progressive_frame"]) / len(flags)) * 100
                if interlaced_percent == 100:
                    interlaced_percent = "Interlaced (CST)"
                else:
                    interlaced_percent = f"{round(interlaced_percent, 2)}% Interlaced (VST)"
                    vst = True
                for ext in ["log", "d2v", "mpg", "mpeg"]:
                    fp = os.path.splitext(self.file)[0] + "." + ext
                    if os.path.exists(fp):
                        os.unlink(fp)
            l1 = f"- {pycountry.languages.get(alpha_2=t.language).name}, {codec} " + \
                f"({t.format_profile}) {t.width}x{t.height} ({t.other_display_aspect_ratio[0]}) " + \
                f"@ {t.other_bit_rate[0]}{f' ({t.bit_rate_mode})' if t.bit_rate_mode else ''}"
            l2 = f"  {(f'{t.framerate_num}/{t.framerate_den}' if t.framerate_num else t.frame_rate)} FPS " + \
                 f"({'VFR' if vst else t.frame_rate_mode}), {t.color_space}{t.chroma_subsampling.replace(':', '')}" + \
                 f"P{t.bit_depth}, {interlaced_percent if interlaced_percent else t.scan_type}"
            data.append([l1, l2])
        return data

    def get_audio_print(self, audio) -> list:
        if not audio:
            return ["--"]
        data = []
        for t in audio:
            if t.title and "Commentary" in t.title:
                title = t.title
            else:
                title = pycountry.languages.get(alpha_2=t.language).name
            if t.channel_layout:
                channels = float(sum(self.AUDIO_CHANNEL_LAYOUT_WEIGHT.get(x, 1) for x in t.channel_layout.split(" ")))
            else:
                channels = float(t.channel_s)
            bit_rate_mode = f" ({t.bit_rate_mode})" if t.bit_rate_mode else ""
            l1 = f"- {title}, {t.format} {channels} @ {t.other_bit_rate[0]}{bit_rate_mode}"
            data += [("  " + x if i > 0 else x) for i, x in enumerate(textwrap.wrap(l1, 64))]
        return data

    @staticmethod
    def get_preview_images(url: str) -> List[dict]:
        if not url:
            return []
        images = []
        for domain in ["imgbox.com", "beyondhd.co"]:
            if domain not in url.lower():
                continue
            page = scrape(url)
            if domain == "imgbox.com":
                for m in re.finditer('src="(https://thumbs2.imgbox.com.+/)(\\w+)_b.([^"]+)', page):
                    images.append({
                        "url": f"https://imgbox.com/{m.group(2)}",
                        "src": f"{m.group(1)}{m.group(2)}_t.{m.group(3)}"
                    })
            elif domain == "beyondhd.co":
                for m in re.finditer('/image/([^"]+)"\\D+src="(https://.*beyondhd.co/images.+/(\\w+).md.[^"]+)', page):
                    images.append({
                        "url": f"https://beyondhd.co/image/{m.group(1)}",
                        "src": m.group(2)
                    })
            break
        return images

    @staticmethod
    def get_subtitle_print(subs):
        if not subs:
            return ["--"]
        data = []
        for t in subs:
            title = t.title or pycountry.languages.get(alpha_2=t.language).name
            l1 = f"- {title}, {t.format.replace('UTF-8', 'SubRip (SRT)')}"
            data += [("  " + x if i > 0 else x) for i, x in enumerate(textwrap.wrap(l1, 64))]
        return data

    @staticmethod
    def get_chapter_print(chapters):
        return ["--"] if not chapters else [[
            f"- {v}"
        ] for v in chapters]

    def get_chapter_print_short(self, chapters):
        if not chapters:
            return "No"
        if self.chapters_numbered:
            return f"Yes (Numbered 01-{str(len(chapters)).zfill(2)})"
        return f"Yes (Named)"