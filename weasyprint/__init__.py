# coding: utf8
"""
    WeasyPrint
    ==========

    WeasyPrint converts web documents to PDF.

    The public API is what is accessible from this "root" packages
    without importing sub-modules.

    :copyright: Copyright 2011-2012 Simon Sapin and contributors, see AUTHORS.
    :license: BSD, see LICENSE for details.

"""

from __future__ import division, unicode_literals

# Make sure the logger is configured early:
from .logger import LOGGER

# No other import here. For this module, do them in functions/methods instead.
# (This reduces the work for eg. 'weasyprint --help')


VERSION = '0.10a0'
__version__ = VERSION

# Used for 'User-Agent' in HTTP and 'Creator' in PDF
VERSION_STRING = 'WeasyPrint %s (http://weasyprint.org/)' % VERSION



class Resource(object):
    """Common API for creating instances of :class:`HTML` or :class:`CSS`.

    You can just create an instance with a positional argument
    (ie. ``HTML(something)``) and it will try to guess if the input is
    a filename, an absolute URL, or a file-like object.

    Alternatively, you can name the argument so that no guessing is
    involved:

    * ``HTML(filename=foo)`` a filename, absolute or relative to
      the current directory.
    * ``HTML(url=foo)`` an absolute, fully qualified URL.
    * ``HTML(file_obj=foo)`` a file-like object: any object with
      a :meth:`read` method.
    * ``HTML(string=foo)`` a string of HTML source.
      (This argument must be named.)

    Specifying multiple inputs is an error: ``HTML(filename=foo, url=bar)``

    Optional, additional named arguments:

    * ``encoding``: force the character encoding
    * ``base_url``: used to resolve relative URLs. If not passed explicitly,
      try to use the input filename, URL, or ``name`` attribute of
      file objects.

    """


class HTML(Resource):
    """Fetch and parse an HTML document with lxml.

    See :class:`Resource` to create an instance.

    """
    def __init__(self, guess=None, filename=None, url=None, file_obj=None,
                 string=None, tree=None, encoding=None, base_url=None):
        import lxml.html
        from .urls import urlopen

        source_type, source, base_url, protocol_encoding = _select_source(
            guess, filename, url, file_obj, string, tree, base_url)

        if source_type == 'tree':
            result = source
        else:
            if source_type == 'string':
                parse = lxml.html.document_fromstring
            else:
                parse = lxml.html.parse
            if not encoding:
                encoding = protocol_encoding
            parser = lxml.html.HTMLParser(encoding=encoding)
            result = parse(source, parser=parser)
            if result is None:
                raise ValueError('Error while parsing HTML')
        if hasattr(result, 'getroot'):
            result.docinfo.URL = base_url
            result = result.getroot()
        else:
            result.getroottree().docinfo.URL = base_url
        self.root_element = result
        self.base_url = base_url

    def _ua_stylesheet(self):
        from .html import HTML5_UA_STYLESHEET
        return [HTML5_UA_STYLESHEET]

    def _get_document(self, backend, stylesheets=(), ua_stylesheets=None):
        if ua_stylesheets is None:
            ua_stylesheets = self._ua_stylesheet()
        from .document import Document
        return Document(
            backend,
            self.root_element,
            user_stylesheets=list(_parse_stylesheets(stylesheets)),
            user_agent_stylesheets=ua_stylesheets)

    def _write(self, backend, target, stylesheets):
        write_to = self._get_document(backend, stylesheets).write_to
        if target is None:
            import io
            target = io.BytesIO()
            write_to(target)
            return target.getvalue()
        else:
            write_to(target)

    def write_pdf(self, target=None, stylesheets=None):
        """Render the document to PDF.

        :param target:
            a filename, file-like object, or :obj:`None`.
        :param stylesheets:
            a list of user stylsheets, as :class:`CSS` objects, filenames,
            URLs, or file-like objects
        :returns:
            If :obj:`target` is :obj:`None`, a PDF byte string.
        """
        from .backends import MetadataPDFBackend
        return self._write(MetadataPDFBackend, target, stylesheets)

    def write_png(self, target=None, stylesheets=None):
        """Render the document to a single PNG image.

        :param target:
            a filename, file-like object, or :obj:`None`.
        :param stylesheets:
            a list of user stylsheets, as :class:`CSS` objects, filenames,
            URLs, or file-like objects
        :returns:
            If :obj:`target` is :obj:`None`, a PNG byte string.
        """
        from .backends import PNGBackend
        return self._write(PNGBackend, target, stylesheets)

    def get_png_pages(self, stylesheets=None):
        """Render the document to multiple PNG images, one per page.

        :param stylesheets:
            a list of user stylsheets, as :class:`CSS` objects, filenames,
            URLs, or file-like objects
        :returns:
            A generator of ``(width, height, png_bytes)`` tuples, one for
            each page, in order.

        """
        from .backends import PNGBackend
        document = self._get_document(PNGBackend, stylesheets)
        return document.get_png_pages()


class CSS(Resource):
    """Fetch and parse a CSS stylesheet.

    See :class:`Resource` to create an instance. A :class:`CSS` object
    is not useful on its own but can be passed to :meth:`HTML.write_pdf` or
    :meth:`HTML.write_png`.

    """
    def __init__(self, guess=None, filename=None, url=None, file_obj=None,
                 string=None, encoding=None, base_url=None,
                 _check_mime_type=False):
        from .css import PARSER, preprocess_stylesheet
        from .urls import urlopen

        source_type, source, base_url, protocol_encoding = _select_source(
            guess, filename, url, file_obj, string, tree=None,
            base_url=base_url, check_css_mime_type=_check_mime_type)

        kwargs = dict(linking_encoding=encoding,
                      protocol_encoding=protocol_encoding)
        if source_type == 'string':
            if isinstance(source, bytes):
                method = 'parse_stylesheet_bytes'
            else:
                # unicode, no encoding
                method = 'parse_stylesheet'
                kwargs.clear()
        else:
            # file_obj or filename
            method = 'parse_stylesheet_file'
        # TODO: do not keep this?
        self.stylesheet = getattr(PARSER, method)(source, **kwargs)
        self.base_url = base_url
        medium = 'print'  # for @media
        self.rules = list(preprocess_stylesheet(
            medium, base_url, self.stylesheet.rules))
        for error in self.stylesheet.errors:
            LOGGER.warn(error)



def _select_source(guess=None, filename=None, url=None, file_obj=None,
                   string=None, tree=None, base_url=None,
                   check_css_mime_type=False):
    """
    Check that only one input is not None, and return it with the
    normalized ``base_url``.

    """
    from .urls import path2url, ensure_url, url_is_absolute, urlopen

    if base_url is not None:
        base_url = ensure_url(base_url)

    nones = [guess is None, filename is None, url is None,
             file_obj is None, string is None, tree is None]
    if nones == [False, True, True, True, True, True]:
        if hasattr(guess, 'read'):
            type_ = 'file_obj'
        elif url_is_absolute(guess):
            type_ = 'url'
        else:
            type_ = 'filename'
        return _select_source(
            base_url=base_url, check_css_mime_type=check_css_mime_type,
            **{type_: guess})
    if nones == [True, False, True, True, True, True]:
        if base_url is None:
            base_url = path2url(filename)
        return 'filename', filename, base_url, None
    if nones == [True, True, False, True, True, True]:
        file_obj, mime_type, protocol_encoding = urlopen(url)
        if check_css_mime_type and mime_type != 'text/css':
            LOGGER.warn('Unsupported stylesheet type: %s', mime_type)
            return 'string', '', base_url, None
        if base_url is None:
            if hasattr(file_obj, 'geturl'):
                base_url = file_obj.geturl()
            else:
                base_url = url
        return 'file_obj', file_obj, base_url, protocol_encoding
    if nones == [True, True, True, False, True, True]:
        if base_url is None:
            # filesystem file objects have a 'name' attribute.
            name = getattr(file_obj, 'name', None)
            if name:
                base_url = ensure_url(name)
        return 'file_obj', file_obj, base_url, None
    if nones == [True, True, True, True, False, True]:
        return 'string', string, base_url, None
    if nones == [True, True, True, True, True, False]:
        return 'tree', tree, base_url, None

    raise TypeError('Expected exactly one source, got %i' % nones.count(False))


def _parse_stylesheets(stylesheets):
    """Yield parsed stylesheets.

    Accept :obj:`None` or a list of filenames, urls or CSS objects.

    """
    if stylesheets is None:
        return
    for css in stylesheets:
        if hasattr(css, 'stylesheet'):
            yield css
        else:
            yield CSS(css)
