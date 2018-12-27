# -*- coding: utf-8 -*-
# Copyright: (C) 2018 Lovac42
# Support: https://github.com/lovac42/HoochieMama
# License: GNU GPL, version 3 or later; http://www.gnu.org/copyleft/gpl.html
# Version: 0.1.7

# Title is in reference to Seinfeld, no relations to the current slang term.

# CONSTANTS:
SHOW_YOUNG_FIRST="order by ivl asc"
SHOW_MATURE_FIRST="order by ivl desc"
SHOW_LOW_REPS_FIRST="order by reps asc"
SHOW_HIGH_REPS_FIRST="order by reps desc"
SORT_BY_OVERDUES="order by due"

# == User Config =========================================

# Keeps calculating intervals accurate
PRIORITIZE_TODAY = True

# Prevents round-robin scheduling of forgotten cards
PRIORITIZE_ALMOST_FORGOTTEN = True

# Randomize reviews per subdeck, makes custom_sort randomized by chunks.
IMPOSE_SUBDECK_LIMIT = True

# Default: Reviews are sorted by dues and randomized in chunks.
# CUSTOM_SORT = None
CUSTOM_SORT = SHOW_YOUNG_FIRST
# CUSTOM_SORT = SHOW_MATURE_FIRST
# CUSTOM_SORT = SHOW_LOW_REPS_FIRST
# CUSTOM_SORT = SHOW_HIGH_REPS_FIRST
# CUSTOM_SORT = SORT_BY_OVERDUES

# == End Config ==========================================

import random
import anki.sched
from aqt import mw
from anki.utils import ids2str
from aqt.utils import showText
from anki.hooks import wrap
from anki.schedv2 import Scheduler

from anki import version
ANKI21 = version.startswith("2.1.")
on_sync = False


# Turn this on if you are having problems.
def debugInfo(msg):
    # print(msg) #console
    # showText(msg) #Windows
    return


# From: anki.schedv2.py
# Mod:  Various, see logs
def fillRev(self, _old):
    if self._revQueue:
        return True
    if not self.revCount:
        return False
    # Below section is invoked everytime the reviewer is reset (edits, adds, etc)

    # This seem like old comments left behind, and does not affect current versions.
    # Remove these lines for testing
    if self.col.decks.get(self.col.decks.selected(), False)['dyn']:
        # dynamic decks need due order preserved
        return _old(self)

    qc = self.col.conf
    if not qc.get("hoochieMama", False):
        return _old(self)
    debugInfo('using hoochieMama')

    lim = Scheduler._currentRevLimit(self)
    if lim:
        lim = min(self.queueLimit, lim)
        sort_by = CUSTOM_SORT if CUSTOM_SORT else SORT_BY_OVERDUES
        if IMPOSE_SUBDECK_LIMIT:
            self._revQueue = getRevQueuePerSubDeck(self, sort_by, lim)
        else:
            self._revQueue = getRevQueue(self, sort_by, lim)

        if self._revQueue:
            if CUSTOM_SORT and not IMPOSE_SUBDECK_LIMIT:
                self._revQueue.reverse()  # preserve order
            else:
                # fixme: as soon as a card is answered, this is no longer consistent
                r = random.Random()
                # r.seed(self.today) #same seed in case user edits card.
                r.shuffle(self._revQueue)
            return True
    if self.revCount:
        # if we didn't get a card but the count is non-zero,
        # we need to check again for any cards that were
        # removed from the queue but not buried
        self._resetRev()
        return self._fillRev()


# In the world of blackjack, “penetration”, or “deck penetration”,
# is the amount of cards that the dealer cuts off, relative to the cards dealt out.
def getRevQueue(self, sortBy, penetration):
    debugInfo('v2 queue builder')
    deck_list = ids2str(self.col.decks.active())
    rev_queue = []

    if PRIORITIZE_TODAY:
        dueToToday = self.col.db.list("""
select id from cards where
did in %s and queue = 2 and due = ?
%s limit ?""" % (deck_list, sortBy),
                self.today, penetration)
        rev_queue.extend(dueToToday)
        penetration -= len(dueToToday)

    if PRIORITIZE_ALMOST_FORGOTTEN:
        if not rev_queue:
            excluded_ids = ids2str([-1])
        else:
            excluded_ids = ids2str(rev_queue)

        dueToAlmostForgotten = self.col.db.list("""select id from cards where
did in %s and id not in %s and queue = 2 and due > ? - ivl and due <= ?
order by (? - due) / ivl limit ?""" % (deck_list, excluded_ids),
                                self.today, self.today,
                                self.today,
                                penetration)
        rev_queue.extend(dueToAlmostForgotten)
        penetration -= len(dueToAlmostForgotten)

    if not rev_queue:
        excluded_ids = ids2str([-1])
    else:
        excluded_ids = ids2str(rev_queue)
    dueToRest = self.col.db.list("""
select id from cards where
did in %s and id not in %s and queue = 2 and due <= ?
%s limit ?""" % (deck_list, excluded_ids, sortBy),
            self.today, penetration)
    rev_queue.extend(dueToRest)

    return rev_queue  # Order needs tobe reversed for custom sorts


def getRevQueuePerSubDeck(self,sortBy,penetration):
    debugInfo('per subdeck queue builder')
    deck_list = ids2str(self.col.decks.active())
    rev_queue = []

    if PRIORITIZE_TODAY:
        dueToToday = self.col.db.all("""
    select id, did from cards where
    did in %s and queue = 2 and due = ?
    %s limit ?""" % (deck_list, sortBy),
                                      self.today, penetration)
        rev_queue.extend(dueToToday)
        penetration -= len(dueToToday)

    if PRIORITIZE_ALMOST_FORGOTTEN:
        if not rev_queue:
            excluded_ids = ids2str([-1])
        else:
            excluded_ids = ids2str([el[0] for el in rev_queue])

        dueToAlmostForgotten = self.col.db.all("""
    select id, did from cards where
    did in %s and id not in %s and queue = 2 and due > ? - ivl and due <= ?
    order by (? - due) / ivl limit ?""" % (deck_list, excluded_ids),
                                                self.today, self.today, self.today,
                                                penetration)
        rev_queue.extend(dueToAlmostForgotten)
        penetration -= len(dueToAlmostForgotten)

    if not rev_queue:
        excluded_ids = ids2str([-1])
    else:
        excluded_ids = ids2str([el[0] for el in rev_queue])
    dueToRest = self.col.db.all("""
    select id, did from cards where
    did in %s and id not in %s and queue = 2 and due <= ?
    %s limit ?""" % (deck_list, excluded_ids, sortBy),
                                 self.today, penetration)
    rev_queue.extend(dueToRest)

    limited_rev_queue = []
    decks = {deck['id']: deck for deck in self.col.decks.all()}
    decks_used_limits = {did: 0 for did in decks.keys()}
    decks_limits = {did: _deckRevLimit(self, d) for did, d in decks.items()}
    for item in rev_queue:
        did = item[1]
        if decks_limits[did] <= decks_used_limits[did]:
            continue

        over_limit = False
        for parent in self.col.decks.parents(did):
            if decks_limits[parent['id']] <= decks_used_limits[parent['id']]:
                over_limit = True
                break
        if over_limit:
            continue

        limited_rev_queue.append(item[0])
        decks_used_limits[did] += 1
        for parent in self.col.decks.parents(did):
            decks_used_limits[parent['id']] += 1

    if not limited_rev_queue and rev_queue:
        return getRevQueuePerSubDeck(self, sortBy, penetration * 2)[:penetration]

    return limited_rev_queue


def _deckRevLimit(self, d):
    if not d: return 0  # invalid deck selected?
    if d['dyn']: return 99999

    c = self.col.decks.confForDid(d['id'])
    return max(0, c['rev']['perDay'] - d['revToday'][1])


def deckRevLimitSingle(self, deck, parentLimit=None, *, _old):
    if IMPOSE_SUBDECK_LIMIT:
        from anki.sched import Scheduler
        return Scheduler._deckRevLimitSingle(self, deck)
    else:
        return _old(self, deck, parentLimit)


anki.sched.Scheduler._fillRev = wrap(anki.sched.Scheduler._fillRev, fillRev, 'around')
if ANKI21:
    import anki.schedv2
    anki.schedv2.Scheduler._deckRevLimitSingle = wrap(anki.schedv2.Scheduler._deckRevLimitSingle, deckRevLimitSingle, 'around')
    anki.schedv2.Scheduler._fillRev = wrap(anki.schedv2.Scheduler._fillRev, fillRev, 'around')


# This monitor sync start/stops
oldSync = anki.sync.Syncer.sync


def onSync(self):
    global on_sync
    on_sync = True
    ret = oldSync(self)
    on_sync = False
    return ret


anki.sync.Syncer.sync = onSync


##################################################
#
#  GUI stuff, adds preference menu options
#
#################################################
import aqt
import aqt.preferences
from aqt.qt import *


if ANKI21:
    from PyQt5 import QtCore, QtGui, QtWidgets
else:
    from PyQt4 import QtCore, QtGui as QtWidgets


def setupUi(self, Preferences):
    r=self.gridLayout_4.rowCount()
    self.hoochieMama = QtWidgets.QCheckBox(self.tab_1)
    self.hoochieMama.setText(_('Hoochie Mama! Randomize Queue'))
    self.hoochieMama.toggled.connect(lambda:toggle(self))
    self.gridLayout_4.addWidget(self.hoochieMama, r, 0, 1, 3)


def __init__(self, mw):
    qc = self.mw.col.conf
    cb = qc.get("hoochieMama", 0)
    self.form.hoochieMama.setCheckState(cb)


def accept(self):
    qc = self.mw.col.conf
    qc['hoochieMama'] = self.form.hoochieMama.checkState()


def toggle(self):
    checked = not self.hoochieMama.checkState() == 0
    if checked:
        try:
            self.serenityNow.setCheckState(0)
        except:
            pass


aqt.forms.preferences.Ui_Preferences.setupUi = wrap(aqt.forms.preferences.Ui_Preferences.setupUi, setupUi, "after")
aqt.preferences.Preferences.__init__ = wrap(aqt.preferences.Preferences.__init__, __init__, "after")
aqt.preferences.Preferences.accept = wrap(aqt.preferences.Preferences.accept, accept, "before")
