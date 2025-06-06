from loguru import logger
import hashlib
import os
import asyncio
from changedetectionio import strtobool
from changedetectionio.content_fetchers.exceptions import BrowserStepsInUnsupportedFetcher, EmptyReply, Non200ErrorCodeReceived
from changedetectionio.content_fetchers.base import Fetcher


# "html_requests" is listed as the default fetcher in store.py!
class fetcher(Fetcher):
    fetcher_description = "Basic fast Plaintext/HTTP Client"

    def __init__(self, proxy_override=None, custom_browser_connection_url=None):
        super().__init__()
        self.proxy_override = proxy_override
        # browser_connection_url is none because its always 'launched locally'

    def _run_sync(self,
            url,
            timeout,
            request_headers,
            request_body,
            request_method,
            ignore_status_codes=False,
            current_include_filters=None,
            is_binary=False,
            empty_pages_are_a_change=False):
        """Synchronous version of run - the original requests implementation"""

        import chardet
        import requests
        from requests.exceptions import ProxyError, ConnectionError, RequestException

        if self.browser_steps_get_valid_steps():
            raise BrowserStepsInUnsupportedFetcher(url=url)

        proxies = {}

        # Allows override the proxy on a per-request basis
        # https://requests.readthedocs.io/en/latest/user/advanced/#socks
        # Should also work with `socks5://user:pass@host:port` type syntax.

        if self.proxy_override:
            proxies = {'http': self.proxy_override, 'https': self.proxy_override, 'ftp': self.proxy_override}
        else:
            if self.system_http_proxy:
                proxies['http'] = self.system_http_proxy
            if self.system_https_proxy:
                proxies['https'] = self.system_https_proxy

        session = requests.Session()

        if strtobool(os.getenv('ALLOW_FILE_URI', 'false')) and url.startswith('file://'):
            from requests_file import FileAdapter
            session.mount('file://', FileAdapter())
        try:
            r = session.request(method=request_method,
                                data=request_body.encode('utf-8') if type(request_body) is str else request_body,
                                url=url,
                                headers=request_headers,
                                timeout=timeout,
                                proxies=proxies,
                                verify=False)
        except Exception as e:
            msg = str(e)
            if proxies and 'SOCKSHTTPSConnectionPool' in msg:
                msg = f"Proxy connection failed? {msg}"
            raise Exception(msg) from e

        # If the response did not tell us what encoding format to expect, Then use chardet to override what `requests` thinks.
        # For example - some sites don't tell us it's utf-8, but return utf-8 content
        # This seems to not occur when using webdriver/selenium, it seems to detect the text encoding more reliably.
        # https://github.com/psf/requests/issues/1604 good info about requests encoding detection
        if not is_binary:
            # Don't run this for PDF (and requests identified as binary) takes a _long_ time
            if not r.headers.get('content-type') or not 'charset=' in r.headers.get('content-type'):
                encoding = chardet.detect(r.content)['encoding']
                if encoding:
                    r.encoding = encoding

        self.headers = r.headers

        if not r.content or not len(r.content):
            logger.debug(f"Requests returned empty content for '{url}'")
            if not empty_pages_are_a_change:
                raise EmptyReply(url=url, status_code=r.status_code)
            else:
                logger.debug(f"URL {url} gave zero byte content reply with Status Code {r.status_code}, but empty_pages_are_a_change = True")

        # @todo test this
        # @todo maybe you really want to test zero-byte return pages?
        if r.status_code != 200 and not ignore_status_codes:
            # maybe check with content works?
            raise Non200ErrorCodeReceived(url=url, status_code=r.status_code, page_html=r.text)

        self.status_code = r.status_code
        if is_binary:
            # Binary files just return their checksum until we add something smarter
            self.content = hashlib.md5(r.content).hexdigest()
        else:
            self.content = r.text

        self.raw_content = r.content

    async def run(self,
            url,
            timeout,
            request_headers,
            request_body,
            request_method,
            ignore_status_codes=False,
            current_include_filters=None,
            is_binary=False,
            empty_pages_are_a_change=False):
        """Async wrapper that runs the synchronous requests code in a thread pool"""
        
        loop = asyncio.get_event_loop()
        
        # Run the synchronous _run_sync in a thread pool to avoid blocking the event loop
        await loop.run_in_executor(
            None,  # Use default ThreadPoolExecutor
            lambda: self._run_sync(
                url=url,
                timeout=timeout,
                request_headers=request_headers,
                request_body=request_body,
                request_method=request_method,
                ignore_status_codes=ignore_status_codes,
                current_include_filters=current_include_filters,
                is_binary=is_binary,
                empty_pages_are_a_change=empty_pages_are_a_change
            )
        )

    def quit(self, watch=None):

        # In case they switched to `requests` fetcher from something else
        # Then the screenshot could be old, in any case, it's not used here.
        # REMOVE_REQUESTS_OLD_SCREENSHOTS - Mainly used for testing
        if strtobool(os.getenv("REMOVE_REQUESTS_OLD_SCREENSHOTS", 'true')):
            screenshot = watch.get_screenshot()
            if screenshot:
                try:
                    os.unlink(screenshot)
                except Exception as e:
                    logger.warning(f"Failed to unlink screenshot: {screenshot} - {e}")

