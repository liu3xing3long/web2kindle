# !/usr/bin/env python
# -*- encoding: utf-8 -*-
# vim: set et sw=4 ts=4 sts=4 ff=unix fenc=utf8:
# Author: Vincent<vincent8280@outlook.com>
#         http://wax8280.github.io
# Created on 2018/1/10 14:18
import os
import re
import time
from copy import deepcopy
from queue import Queue, PriorityQueue
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup

from web2kindle import MAIN_CONFIG
from web2kindle.libs.crawler import Crawler, RetryDownload, Task
from web2kindle.libs.db import ArticleDB
from web2kindle.libs.html2kindle import HTML2Kindle
from web2kindle.libs.send_email import SendEmail2Kindle
from web2kindle.libs.utils import write, md5string, load_config, check_config, format_file_name
from web2kindle.libs.log import Log

SCRIPT_CONFIG = load_config('./web2kindle/config/jianshu_user.yml')
LOG = Log("jianshu_user")
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/'
                  '61.0.3163.100 Safari/537.36'
}

check_config(MAIN_CONFIG, SCRIPT_CONFIG, 'SAVE_PATH', LOG)
ARTICLE_ID_SET = set()

ORDER_TOP = 'top'
ORDER_COMMENT = 'commented_at'
ORDER_ADD = 'added_at'
API_URL = 'https://www.jianshu.com/u/{}?order_by={}&page={}'
BASE_URL = 'https://www.jianshu.com/u/{}'


def main(zhuanti_list, start, end, kw):
    """start默认1；end为结束页数，每页9个"""
    iq = PriorityQueue()
    oq = PriorityQueue()
    result_q = Queue()
    crawler = Crawler(iq, oq, result_q,
                      MAIN_CONFIG.get('PARSER_WORKER', 1),
                      MAIN_CONFIG.get('DOWNLOADER_WORKER', 1),
                      MAIN_CONFIG.get('RESULTER_WORKER', 1))

    start = int(start)
    end = int(end)

    for zhuanti in zhuanti_list:
        new_header = deepcopy(DEFAULT_HEADERS)
        new_header.update({'Referer': BASE_URL.format(zhuanti)})

        # 以专题的数字作为子文件名
        save_path = os.path.join(SCRIPT_CONFIG['SAVE_PATH'], str(zhuanti))

        if kw.get('order_by') == 'comment':
            order_by = ORDER_COMMENT
        elif kw.get('order_by') == 'add':
            order_by = ORDER_ADD
        elif kw.get('order_by') == 'top':
            order_by = ORDER_TOP
        else:
            # 默认add
            order_by = ORDER_ADD

        task = Task.make_task({
            'url': API_URL.format(zhuanti, order_by, start),
            'method': 'GET',
            'meta': {'headers': new_header, 'verify': False},
            'parser': parser_list,
            'priority': 0,
            'save': {'cursor': start,
                     'save_path': save_path,
                     'start': start,
                     'end': end,
                     'kw': kw,
                     'name': zhuanti,
                     'order_by': order_by},
            'retry': 3,
        })

        iq.put(task)

        # Init DB
        with ArticleDB(save_path, VERSION=0) as db:
            _ = db.select_all_article_id()

        # 利用集合去重
        if _:
            for each in _:
                ARTICLE_ID_SET.add(each[0])

    # 开始爬虫
    crawler.start()

    # 开始制作电子书
    for zhuanti in zhuanti_list:
        items = []
        save_path = os.path.join(SCRIPT_CONFIG['SAVE_PATH'], str(zhuanti))
        with ArticleDB(save_path, VERSION=0) as db:
            # 读取所有文章
            items.extend(db.select_article())
            # 从数据库中获取专题名字
            book_name = db.select_meta('BOOK_NAME')
            # 更新数据库版本
            db.increase_version()
            # 数据库收尾工作
            db.reset()

        if items:
            with HTML2Kindle(items, save_path, book_name, MAIN_CONFIG.get('KINDLEGEN_PATH')) as html2kindle:
                html2kindle.make_metadata(window=kw.get('window', 50))
                html2kindle.make_book_multi(save_path)

            if kw.get('email'):
                with SendEmail2Kindle() as s:
                    s.send_all_mobi(save_path)
        else:
            LOG.log_it('无新项目', 'INFO')


def parser_list(task):
    response = task['response']
    new_tasks = []
    to_next = True

    if not response:
        raise RetryDownload

    try:
        text = response.text
        bs = BeautifulSoup(text, 'lxml')
    except Exception as e:
        LOG.log_it('解析网页出错（如一直出现，而且浏览器能正常访问，可能是网站网站代码升级，请通知开发者。）ERRINFO:{}'
                   .format(str(e)), 'WARN')
        raise RetryDownload

    book_name = bs.title.string if bs.title else task['save']['name']

    # 插入文集名字
    with ArticleDB(task['save']['save_path']) as article_db:
        article_db.insert_meta_data(['BOOK_NAME', format_file_name('简书专题_' + book_name)], update=False)

    # 顺序反向
    items = bs.select('a.title')
    items.reverse()

    for item in items:
        # 如果已经在数据库中，则不下载
        url = 'https://www.jianshu.com' + item.attrs['href']
        if md5string(url) in ARTICLE_ID_SET:
            to_next = False
            continue

        try:
            title = item.string
        except:
            LOG.log_it('解析标题出错（如一直出现，而且浏览器能正常访问，可能是网站网站代码升级，请通知开发者。）', 'WARN')
            raise RetryDownload

        new_task = Task.make_task({
            'url': url,
            'method': 'GET',
            'meta': task['meta'],
            'parser': parser_content,
            'resulter': resulter_content,
            'priority': 5,
            'save': task['save'],
            'title': title,
        })
        new_tasks.append(new_task)

    # 下一页
    if to_next and len(items) != 0:
        if task['save']['cursor'] < task['save']['end']:
            next_page_task = deepcopy(task)
            next_page_task.update(
                {'url': API_URL.format(task['save']['name'], task['save']['order_by'], task['save']['cursor'] + 1)})
            next_page_task['save'].update({'cursor': next_page_task['save']['cursor'] + 1})
            new_tasks.append(next_page_task)

    return None, new_tasks


def parser_content(task):
    title = task['title']
    new_tasks = []

    response = task['response']
    if not response:
        raise RetryDownload

    bs = BeautifulSoup(response.text, 'lxml')

    content_tab = bs.select('.show-content')
    if content_tab:
        content = str(content_tab[0])
    else:
        LOG.log_it("不能找到文章的内容。（如一直出现，而且浏览器能正常访问网站，可能是网站代码升级，请通知开发者。）", 'WARN')
        raise RetryDownload

    author_name = bs.select('.post .author .name a')[0].string if bs.select('.post .author .name a') else ''
    voteup_count = bs.select('.post .author .meta .likes-count')[0].string if bs.select(
        '.post .author .meta .likes-count') else ''
    created_time = bs.select('.post .author .meta .publish-time')[0].string if bs.select(
        '.post .author .meta .publish-time') else ''
    article_url = task['url']

    download_img_list, content = format_content(content, task)

    item = [md5string(article_url), title, content, created_time, voteup_count, author_name, int(time.time() * 100000)]

    if task['save']['kw'].get('img', True):
        img_header = deepcopy(DEFAULT_HEADERS)
        img_header.update({'Referer': response.url})
        for img_url in download_img_list:
            new_tasks.append(Task.make_task({
                'url': img_url,
                'method': 'GET',
                'meta': {'headers': img_header, 'verify': False},
                'parser': parser_downloader_img,
                'resulter': resulter_downloader_img,
                'save': task['save'],
                'priority': 10,
            }))

    task.update({"parsed_data": item})
    return task, new_tasks


def resulter_content(task):
    LOG.log_it("正在将任务 {} 插入数据库".format(task['tid']), 'INFO')
    with ArticleDB(task['save']['save_path']) as article_db:
        article_db.insert_article(task['parsed_data'])


def parser_downloader_img(task):
    return task, None


def resulter_downloader_img(task):
    if '.' not in urlparse(task['response'].url).path[1:]:
        name = urlparse(task['response'].url).path[1:] + '.jpg'
    else:
        name = urlparse(task['response'].url).path[1:]

    write(os.path.join(task['save']['save_path'], 'static'), name, task['response'].content, mode='wb')


def convert_link(x):
    # 避免重复转换。忽略已经转换为本地的链接
    if x.group(1).startswith('.'):
        return 'src={}'.format(x.group(1))
    else:
        # FIXME:图片格式识别。因为简书里面有些图片是没有后缀名的。kindlegen不能识别没有后缀名的格式
        if '.' not in urlparse(x.group(1)).path[1:]:
            name = urlparse(x.group(1)).path[1:] + '.jpg'
        else:
            name = urlparse(x.group(1)).path[1:]
        return 'src="./static/{}"'.format(name)


def format_content(content: str, task):
    download_img_list = []
    # 换行格式化
    content = content.replace('</p><br/><p>', '<br/>').replace('</p><p><br/>', '').replace('</p><p><br>', '')
    content = re.sub('(<br>)+', '<br/>', content)
    content = re.sub('(<br/>)+', '<br/>', content)

    bs2 = BeautifulSoup(content, 'lxml')
    # 去除格式
    for _ in bs2.select('.image-container'):
        del _['style']

    for _ in bs2.select('.image-container-fill'):
        del _['style']

    # 居中图片
    for tab in bs2.select('img'):
        tab.wrap(bs2.new_tag('div', style='text-align:center;'))
        tab['style'] = "display: inline-block;"

        # 删除gif
        if task['save']['kw']['gif'] is False:
            if tab.has_attr('data-original-src'):
                if 'gif' in tab['data-original-src']:
                    tab.decompose()
            elif tab.has_attr('src'):
                if 'gif' in tab['src']:
                    tab.decompose()

    content = str(bs2)
    # bs4会自动加html和body 标签
    content = re.sub('<html><body>(.*?)</body></html>', lambda x: x.group(1), content, flags=re.S)

    # 需要下载的静态资源
    download_img_list.extend(re.findall('data-original-src="(.*?)"', content))
    download_img_list.extend(re.findall('src="(.*?)"', content))

    # 更换为本地相对路径
    content = re.sub('data-original-src="(.*?)"', convert_link, content)
    content = re.sub('src="(.*?)"', convert_link, content)

    # 超链接的转换
    content = re.sub('link.jianshu.com/\?t=(.*?)"', lambda x: unquote(x.group(1)), content)
    return download_img_list, content
