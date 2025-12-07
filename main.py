from typing import List, Tuple
import requests
from bs4 import BeautifulSoup
import aiohttp
import asyncio
import csv
import json
from os.path import exists
from pathlib import Path

PAPERHELP_ARTICLE_URL = (
    "https://www.paperhelp.org/blog/top-50-university-open-courseware-collections.html"
)
DOWNLOAD_DIR = Path("produced_files")


def paperhelp_scrap(
    paperhelp_url: str = PAPERHELP_ARTICLE_URL,
    csv_file: str = "Top_universities_courseware.csv",
) -> str:
    """Scrapper for Paperhelp website to extract links"""
    if exists(csv_file):
        return csv_file
    else:
        source = requests.get(paperhelp_url).text
        soup = BeautifulSoup(source, "lxml")
        urls = []
        summaries = soup.find_all("p")
        for summary in summaries:
            if summary.a is not None:
                url = summary.a.get("href")
                urls.append(url)
        urls = urls[2:]

    with open(csv_file, "r") as f:
        writer = csv.writer(f)
        writer.writerow("url")
        writer.writerows(urls)

    return csv_file


async def check_link(url: str, asession: aiohttp.ClientSession) -> Tuple[str, str]:
    """Check the availability of links and check credibility of them"""
    try:
        response = await asession.get(url)
        if response.status == 200:
            html = await response.text()
            soup = BeautifulSoup(html, "lxml")
            title = soup.find("title")
            if title is not None:
                if title.text is not None:
                    return url, title.text
            return url, ""
        else:
            return url, str(response.status)
    except Exception as e:
        return url, str(e)


async def check_links(url_files: str):
    urls = []
    with open(url_files) as f:
        reader = csv.reader(f)
        reader.__next__()
        urls.extend(list(reader))

    async with aiohttp.ClientSession() as asession:
        tasks = [check_link(url, asession) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return results


def main():
    DOWNLOAD_DIR.mkdir(exist_ok=True, parents=True)

    paperhelp_universities = paperhelp_scrap()

    c_l = asyncio.run(check_links(paperhelp_universities))


if __name__ == "__main__":
    main()
