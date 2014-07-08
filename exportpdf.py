# -*- coding: utf-8 -*-
#Copyright (c) 2011-14 Walter Bender

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA

import os
import time
import json

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk
from gi.repository import GdkPixbuf
from glib import GError
from gi.repository import Pango
from gi.repository import PangoCairo
import cairo

from gettext import gettext as _

import logging
_logger = logging.getLogger("story-activity")


PAGE_WIDTH = 504
PAGE_HEIGHT = 648
LEFT_MARGIN = 10
TOP_MARGIN = 20


def save_pdf(activity, tmp_file, nick, description=None):
    ''' Output a PDF document from the title, pictures, and descriptions '''

    head = 18
    body = 12

    pdf_surface = cairo.PDFSurface(tmp_file, 504, 648)

    fd = Pango.FontDescription('Sans')
    cr = cairo.Context(pdf_surface)
    cr.set_source_rgb(0, 0, 0)

    show_text(cr, fd, nick, head, LEFT_MARGIN, TOP_MARGIN)
    show_text(cr, fd, time.strftime('%x', time.localtime()),
              body, LEFT_MARGIN, TOP_MARGIN + 3 * head)
    if description is not None:
        show_text(cr, fd, description,
                  body, LEFT_MARGIN, TOP_MARGIN + 4 * head)
    cr.show_page()

    if activity._game.get_mode() == 'array':
        text = ''
        if 'text' in activity.metadata:
            text = activity.metadata['text']
        one_page(activity, cr, fd, body, text)
    else:
        save_page = activity._game.current_image
        for i in range(9):
            activity._game.current_image = i
            text = ''
            if 'text-%d' % i in activity.metadata:
                text = activity.metadata['text-%d' % i]
            page(activity, cr, fd, body, text)
        activity._game.current_image = save_page


def one_page(activity, cr, fd, body, text):
    w = h = int((4 * activity._game._space + 3 * activity._game._dot_size))
    png_surface = activity._game.export()
    cr.save()
    cr.scale(0.5, 0.5)
    for i in range(9):
        y = activity._game._space + int(i / 3.) * \
            (activity._game._dot_size + activity._game._space)
        x = activity._game._space + (i % 3) * \
            (activity._game._dot_size + activity._game._space)
        cr.save()
        cr.set_source_surface(activity._game._dots[i].images[0], x, y)
        cr.rectangle(x, y, activity._game._dot_size, activity._game._dot_size)
        cr.fill()
        cr.restore()
    cr.scale(1, 1)
    cr.restore()

    show_text(cr, fd, text, body, LEFT_MARGIN, 300)

    cr.show_page()


def page(activity, cr, fd, body, text):
    w = h = int((4 * activity._game._space + 3 * activity._game._dot_size))
    png_surface = activity._game.export()
    cr.save()
    x = int(activity._game._space)
    y = int(activity._game._space)
    cr.scale(0.5, 0.5)
    cr.set_source_surface(
        activity._game._Dots[activity._game.current_image].images[0], x, y)
    cr.scale(1, 1)
    cr.rectangle(LEFT_MARGIN, TOP_MARGIN, w, h)
    cr.fill()
    cr.restore()

    show_text(cr, fd, text, body, LEFT_MARGIN, 200)

    cr.show_page()


def show_text(cr, fd, label, size, x, y):
    pl = PangoCairo.create_layout(cr)
    fd.set_size(int(size * Pango.SCALE))
    pl.set_font_description(fd)
    if type(label) == str or type(label) == unicode:
        pl.set_text(label.replace('\0', ' '), -1)
    else:
        pl.set_text(str(label), -1)
    pl.set_width((PAGE_WIDTH - LEFT_MARGIN * 2) * Pango.SCALE)
    cr.save()
    cr.translate(x, y)
    PangoCairo.update_layout(cr, pl)
    PangoCairo.show_layout(cr, pl)
    cr.restore()
