"""Debounced life evidence for Trio. Detection loss is never itself a death."""
from collections import deque


class LifeTracker:
    def __init__(self, history_seconds=20, missing_seconds=5, stable_samples=3, respawn_min_seconds=12):
        self.history_seconds=history_seconds
        self.missing_seconds=missing_seconds
        self.stable_samples=stable_samples
        self.history=deque()
        self.life_id=0
        self.alive=False
        self.missing_since=None
        self.present_samples=0
        self.last_stamp=None
        self.candidate_frames=[]
        self.last_world={}
        self.events=[]
        self.awaiting_respawn=False
        self.respawn_min_seconds=respawn_min_seconds
        self.death_confirmed_at=None

    def update(self, stamp, present, state, frame=None, world=None):
        if stamp is None or (self.last_stamp is not None and stamp <= self.last_stamp):
            return []
        self.last_stamp=stamp
        # Keep evidence through uncertain state detection. Unknown still resets
        # death inference below, but must not erase seconds of the death clip.
        self.history.append((stamp,frame,world or {}))
        while self.history and stamp-self.history[0][0]>self.history_seconds:
            self.history.popleft()
        if state!='match':
            self.present_samples=0
            if str(state or '').startswith('end_') or state=='lobby':
                # Preserve an already observed absence for finish(); the result
                # is its confirmation, not a reason to erase the candidate.
                return []
            self.missing_since=None
            self.candidate_frames=[]
            return []
        if (world or {}).get('respawn_ui'):
            self.present_samples=0
            if self.alive:
                self.missing_since=self.missing_since or stamp
                self.candidate_frames=list(self.history)
                event=self._death('RESPAWN_COUNTDOWN_UI',stamp,.95)
                event.update(event='DEATH_CONFIRMED',verified=True,
                             observation_limit='Positive respawn countdown confirms a death; exact lethal frame/cause remain unknown.')
                self.alive=False
                self.awaiting_respawn=True
                self.death_confirmed_at=stamp
                self.missing_since=None
                self.events.append(event)
                return [event]
            return []
        if not present:
            self.present_samples=0
            if self.alive and self.missing_since is None:
                self.missing_since=stamp
                self.candidate_frames=list(self.history)
            return []
        # Actual Trio recordings show a 15-second countdown. A teammate falsely
        # labelled player during this countdown must not create another life.
        # This lower bound is conservative/heuristic, not an OCR timer reading.
        if self.awaiting_respawn and stamp-self.death_confirmed_at<self.respawn_min_seconds:
            self.present_samples=0
            return []
        self.present_samples+=1
        self.last_world=world or {}
        if self.present_samples<self.stable_samples:
            return []
        emitted=[]
        if self.alive and self.missing_since is not None:
            if stamp-self.missing_since>=self.missing_seconds:
                event=self._death('PLAYER_REAPPEARED',stamp,.7)
                emitted.append(event)
                self.life_id+=1
                emitted.append({'event':'RESPAWN_INFERRED','life_id':self.life_id,'timestamp':stamp})
                self.history.clear()
            self.missing_since=None
            self.candidate_frames=[]
        elif not self.alive:
            self.life_id+=1
            self.alive=True
            emitted.append({'event':'RESPAWN_CONFIRMED' if self.awaiting_respawn else 'SPAWN_CONFIRMED',
                            'life_id':self.life_id,'timestamp':stamp})
            self.awaiting_respawn=False
            self.death_confirmed_at=None
        self.events.extend(emitted)
        return emitted

    def _death(self, confirmation, stamp, confidence):
        return {'event':'DEATH_INFERRED','life_id':self.life_id,
                'timestamp':self.missing_since,'confirmed_at':stamp,
                'confirmation':confirmation,'confidence':confidence,
                'confidence_kind':'heuristic_uncalibrated', 'verified':False,
                'frames':list(self.candidate_frames),
                'observation_limit':'Player absence and reappearance support this inference; no HP/respawn UI detector.'}

    def finish(self, stamp):
        emitted=[]
        if self.alive and self.missing_since is not None and stamp-self.missing_since>=self.missing_seconds:
            emitted.append(self._death('MATCH_RESULT',stamp,.6))
        self.events.extend(emitted)
        return emitted
