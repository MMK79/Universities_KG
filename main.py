from typing import List, Tuple
import requests
from bs4 import BeautifulSoup, BeautifulStoneSoup
import aiohttp
import asyncio
import csv
import json
from os.path import exists
from pathlib import Path

PAPERHELP_ARTICLE_URL = (
    "https://www.paperhelp.org/blog/top-50-university-open-courseware-collections.html"
)
LEARNING_PLATFORM_URL = "https://www.learningplatforms.net/a-z/"
DOWNLOAD_DIR = Path("produced_files")


def paperhelp_scrap(
    paperhelp_url: str = PAPERHELP_ARTICLE_URL,
    csv_file: str = "urls.csv",
) -> str:
    """Scrapper for Paperhelp website to extract links"""
    write_path = DOWNLOAD_DIR / csv_file
    if exists(write_path):
        return csv_file

    else:
        source = requests.get(paperhelp_url).text
        soup = BeautifulSoup(source, "lxml")
        urls = []
        summaries = soup.find_all("p")
        for summary in summaries:
            if summary.a is not None:
                url = summary.a.get("href")
                urls.append([url])
        urls = urls[2:]

    write_path = DOWNLOAD_DIR / csv_file
    with open(write_path, "w") as f:
        writer = csv.writer(f)
        writer.writerow(["url"])
        writer.writerows(urls)

    return csv_file


def learning_platform_posts_scrap(
    learning_platform_url: str = LEARNING_PLATFORM_URL,
    csv_file: str = "learning_platform_urls.csv",
):
    write_path = DOWNLOAD_DIR / csv_file
    if exists(write_path):
        return csv_file

    source = requests.get(learning_platform_url).text
    soup = BeautifulSoup(source, "lxml")
    urls = []
    posts = soup.find_all(class_="sitemap-item")
    for post in posts:
        if post.a is not None:
            link = post.a.get("href")
            urls.append([link])

    with open(write_path, "w") as f:
        writer = csv.writer(f)
        writer.writerow(["url"])
        writer.writerows(urls)
    return csv_file


def learning_platform_post_scrap(
    url: str,
):
    source = requests.get(url).text
    soup = BeautifulSoup(source, "lxml")
    article_text = soup.find_all(
        class_="et_pb_module et_pb_post_content et_pb_post_content_0_tb_body"
    )

    conditions = ["i0.wp.com/", "facebook", "twitter", "linkedin"]
    links = []
    urls = []

    for i in article_text:
        links = i.find_all("a")

    for link in links:
        clean_url = link.get("href")
        test = [clean_url for condition in conditions if condition not in clean_url]
        if len(test) == len(conditions):
            urls.append(clean_url)

    return urls


def gather_urls(
    urls_csv: str = "learning_platform_urls.csv",
    processed_file: str = "processed_links.csv",
):
    write_path = DOWNLOAD_DIR / urls_csv
    with open(write_path) as f:
        reader = csv.reader(f)
        reader.__next__()
        urls = list(reader)

    links = []
    for url in urls:
        link = learning_platform_post_scrap(url[0])
        links.append(link)

    write_path = DOWNLOAD_DIR / processed_file
    with open(write_path, "w") as f:
        writer = csv.writer(f)
        writer.writerow(["url"])
        writer.writerows(links)

    return processed_file


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
    # main()
    # learning_platform_posts_scrap()
    paperhelp_scrap()
    learning_platform_posts_scrap()
    gather_urls()
