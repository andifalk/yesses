from typing import List
import re
import requests
import threading
from urllib.parse import urlparse

from urllib3.util import connection
from contextlib import contextmanager

_orig_create_connection = connection.create_connection


@contextmanager
def force_ip_connection(domain, ip):
    def patched_create_connection(address, *args, **kwargs):
        """Wrap urllib3's create_connection to resolve the name elsewhere"""
        # resolve hostname to an ip address; use your own
        # resolver here, as otherwise the system resolver will be used.
        host, port = address
        if host == domain:
            hostname = ip
        else:
            hostname = host
        return _orig_create_connection((hostname, port), *args, **kwargs)

    connection.create_connection = patched_create_connection
    yield
    connection.create_connection = _orig_create_connection


def clean_expression(expr):
    return re.sub(r'''\s+''', ' ', expr).strip()


def filter_origins(origins: list) -> dict:
    """
    Removes duplicated origins. First some origins are reachable through IPv4 and IPv6
    and second some web servers just redirect to another origin.
    :param origins:
    :return: origins without any duplication
    """
    filtered_origins = dict()
    for origin in origins:
        parsed_url = UrlParser(origin['url'])
        with force_ip_connection(origin['domain'], origin['ip']):
            r = requests.get(parsed_url.url_without_path)
            forwareded_parsed_url = UrlParser(r.url)
            if forwareded_parsed_url.url_without_path not in filtered_origins.keys() \
                    and int(r.status_code / 100) != 4 and int(r.status_code / 100) != 5:
                url = f"{forwareded_parsed_url.url_without_path}/"
                filtered_origins[forwareded_parsed_url.url_without_path] = {'url': url,
                                                                            'domain': forwareded_parsed_url.domain,
                                                                            'ip': origin['ip']}
    return filtered_origins


def request_is_text(r: requests.Response) -> bool:
    if re.search(r"(^text/.*|^application/.*|^image/svg\+xml$)", r.headers['content-type']):
        return True
    return False


def read_file(list: str) -> List[str]:
    with open(list) as file:
        dir_list = file.readlines()
        dir_list = [line.strip('\n') for line in dir_list if not line.startswith('#')]

    return dir_list


class UrlParser:
    STANDARD_PORTS = {'http': 80, 'https': 443}

    def __init__(self, url: str):
        self.parsed = urlparse(url)  # type: ParseResult
        self.netloc = self.parsed.netloc  # type: str
        self.path = self.parsed.path  # type: str
        self.arguments = self.parsed.query  # type: str
        self.scheme = self.parsed.scheme  # type: str

        tmp_path = self.path
        if not tmp_path.startswith('/'):
            tmp_path = f"/{tmp_path}"

        # calculate the depth of the path
        self.path_depth = len(tmp_path.split('/')) - 1
        if tmp_path.endswith('/'):
            self.path_depth -= 1

        # concatenate the path with the GET parameters
        self.path_with_args = tmp_path  # type: str
        if self.parsed.query != '':
            self.path_with_args = f"{tmp_path}?{self.arguments}"

        # cut the file ending from the path
        index = self.parsed.path.rfind('.')
        if index != -1:
            self.file_ending = self.parsed.path[index:]
        else:
            self.file_ending = ''

        # retrieve the base domain (domain which was passed without the port and 'www.' prefix)
        tmp = self.parsed.netloc.split(':')
        self.base_domain = tmp[0]
        split = self.base_domain.split('.')
        if split[0] == "www":
            self.base_domain = '.'.join(split[1:])

        # the url with the protocol and port (if no port is specified use a standard port)
        self.url_without_path = self.parsed.netloc
        if self.scheme == '':
            self.scheme = 'http'
        if len(tmp) == 1:
            self.url_without_path = f"{self.url_without_path}:{self.STANDARD_PORTS.get(self.scheme, 80)}"

        self.url_without_path = f"{self.scheme}://{self.url_without_path}"

        # retrieve domain name
        self.domain = self.netloc.split(':')[0]

    def full_url(self):
        return f"{self.url_without_path}{self.path_with_args}"

    def __eq__(self, other):
        return self.full_url() == other

    def __str__(self):
        return self.full_url()


class ConcurrentSession:

    def __init__(self, threads):
        self._threads = threads
        self._finished = {}
        self._lock = threading.Lock()

    def register_thread(self, ident: int):
        self._lock.acquire()
        self._finished[ident] = False
        self._lock.release()

    def ready(self, ident: int):
        self._lock.acquire()
        self._finished[ident] = True
        self._lock.release()

    def unready(self, ident: int):
        self._lock.acquire()
        self._finished[ident] = False
        self._lock.release()

    def is_ready(self) -> bool:
        for value in self._finished.values():
            if not value:
                return False
        return True
