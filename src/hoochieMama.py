# -*- coding: utf-8 -*-
# Copyright: (C) 2018 Lovac42
# Support: https://github.com/lovac42/HoochieMama
# License: GNU GPL, version 3 or later; http://www.gnu.org/copyleft/gpl.html
# Version: 0.1.7

# Title is in reference to Seinfeld, no relations to the current slang term.

# CONSTANTS:
import math

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


def deckRevLimitSingle(self, deck, parentLimit=None, *, _old):
    qc = self.col.conf
    if not qc.get("hoochieMama", False):
        return _old(self, deck, parentLimit)

    if IMPOSE_SUBDECK_LIMIT:
        from anki.sched import Scheduler
        return Scheduler._deckRevLimitSingle(self, deck)
    else:
        return _old(self, deck, parentLimit)


# From: anki.schedv2.py
def answerRevCard(self, card, ease, *, _old):
    qc = self.col.conf
    if not qc.get("hoochieMama", False):
        return _old(self, card, ease)

    delay = 0
    early = card.odid and (card.odue > self.today)
    type = early and 3 or 1

    card.lastFactor = card.factor
    if not early:
        # We shouldn't update any factors when early review happens
        card.factor = _newFactor(self, card, ease)

    if ease == 1:
        delay = _rescheduleLapse(self, card)
    else:
        _rescheduleRev(self, card, ease, early)

    self._logRev(card, ease, delay, type)


# From: anki.schedv2.py
def nextRevIvl(self, card, ease, fuzz, *, _old):
    qc = self.col.conf
    if not qc.get("hoochieMama", False):
        return _old(self, card, ease, fuzz)

    new_factor = _newFactor(self, card, ease)
    return self._constrainedIvl(card.ivl * new_factor / 1000, self._revConf(card), card.ivl / (card.factor / 1000), fuzz)


# From: anki.schedv2.py
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

    lim = Scheduler._currentRevLimit(self)
    if lim:
        lim = min(self.queueLimit, lim)
        sort_by = CUSTOM_SORT if CUSTOM_SORT else SORT_BY_OVERDUES
        if IMPOSE_SUBDECK_LIMIT:
            self._revQueue = get_rev_queue_per_subdeck(self, sort_by, lim)
        else:
            self._revQueue = _get_rev_queue(self, sort_by, lim)

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


def _newFactor(self, card, ease):
    # R = e ** (-k * t/S)
    # R for t == s should be `good` == 0.90
    # Therefor -k = ln(0.90)

    easy_lower = 0.95
    good_lower = 0.85
    hard_lower = 0.75

    k = math.log(2/(easy_lower + good_lower))

    delayed_by = self._daysLate(card)
    total_time = card.ivl + delayed_by
    predicted_R = math.exp(-k * total_time / card.ivl)

    if predicted_R > easy_lower:
        predicted_ease = 4
    elif predicted_R > good_lower:
        predicted_ease = 3
    elif predicted_R > hard_lower:
        predicted_ease = 2
    else:
        predicted_ease = 1

    if predicted_ease == ease:
        return card.factor
    elif predicted_ease > ease:
        if ease == 3:
            p = math.log(easy_lower)
        elif ease == 2:
            p = math.log(good_lower)
        else:  # ease == 1
            p = math.log(hard_lower)
    else:  # predicted_ease < ease:
        if ease == 4:
            p = math.log(easy_lower)
        elif ease == 3:
            p = math.log(good_lower)
        else:  # ease == 2:
            p = math.log(hard_lower)
    # p = -k * total_time / corrected_ivl
    corrected_ivl = -k * total_time / p

    ivl_power = math.log(card.ivl * (card.factor / 1000)) / math.log(card.factor / 1000)
    factor_multiplier = (corrected_ivl / card.ivl) ** (1 / (ivl_power ** 0.37037))
    factor = int(card.factor * factor_multiplier)
    factor = max(1300, factor)
    factor = min(10000, factor)
    return factor


# From: anki.schedv2.py
def _rescheduleRev(self, card, ease, early):
    # update interval
    card.lastIvl = card.ivl
    if early:
        self._updateEarlyRevIvl(card, ease)
    else:
        card.ivl = self._constrainedIvl(card.ivl * card.factor / 1000, self._revConf(card), card.lastIvl / (card.factor / 1000), fuzz=True)

    card.due = self.today + card.ivl

    # card leaves filtered deck
    self._removeFromFiltered(card)


# From: anki.schedv2.py
def _rescheduleLapse(self, card):
    conf = self._lapseConf(card)

    card.lapses += 1
    suspended = self._checkLeech(card, conf) and card.queue == -1

    card.lastIvl = card.ivl
    card.ivl = card.ivl * card.factor / card.lastFactor

    if conf['delays'] and not suspended:
        card.type = 3
        delay = self._moveToFirstStep(card, conf)
    else:
        # no relearning steps
        self._updateRevIvlOnFail(card, conf)
        self._rescheduleAsRev(card, conf, early=False)
        # need to reset the queue after rescheduling
        if suspended:
            card.queue = -1
        delay = 0

    return delay


# In the world of blackjack, “penetration”, or “deck penetration”,
# is the amount of cards that the dealer cuts off, relative to the cards dealt out.
def _get_rev_queue(self, sort_by, penetration):
    deck_list = ids2str(self.col.decks.active())
    rev_queue = []

    if PRIORITIZE_TODAY:
        dueToToday = self.col.db.list("""
select id from cards where
did in %s and queue = 2 and due = ?
%s limit ?""" % (deck_list, sort_by),
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
%s limit ?""" % (deck_list, excluded_ids, sort_by),
                                 self.today, penetration)
    rev_queue.extend(dueToRest)

    return rev_queue  # Order needs tobe reversed for custom sorts


def get_rev_queue_per_subdeck(self, sort_by, penetration):
    deck_list = ids2str(self.col.decks.active())
    rev_queue = []

    if PRIORITIZE_TODAY:
        dueToToday = self.col.db.all("""
    select id, did from cards where
    did in %s and queue = 2 and due = ?
    %s limit ?""" % (deck_list, sort_by),
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
    %s limit ?""" % (deck_list, excluded_ids, sort_by),
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
        return get_rev_queue_per_subdeck(self, sort_by, penetration * 2)[:penetration]

    return limited_rev_queue


def _deckRevLimit(self, d):
    if not d: return 0  # invalid deck selected?
    if d['dyn']: return 99999

    c = self.col.decks.confForDid(d['id'])
    return max(0, c['rev']['perDay'] - d['revToday'][1])


anki.sched.Scheduler._fillRev = wrap(anki.sched.Scheduler._fillRev, fillRev, 'around')
if ANKI21:
    import anki.schedv2
    anki.schedv2.Scheduler._deckRevLimitSingle = wrap(anki.schedv2.Scheduler._deckRevLimitSingle, deckRevLimitSingle, 'around')
    anki.schedv2.Scheduler._fillRev = wrap(anki.schedv2.Scheduler._fillRev, fillRev, 'around')
    anki.schedv2.Scheduler._answerRevCard = wrap(anki.schedv2.Scheduler._answerRevCard, answerRevCard, 'around')
    anki.schedv2.Scheduler._nextRevIvl = wrap(anki.schedv2.Scheduler._nextRevIvl, nextRevIvl, 'around')


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
