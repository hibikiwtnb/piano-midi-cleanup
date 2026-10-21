(function () {
  "use strict";
  var sessions = [],
    nextSessionId = 1;
  var LEFT = 72,
    ROW = 11,
    HEADER = 18,
    PITCH_MIN = 0,
    PITCH_MAX = 127,
    HEIGHT = HEADER + (PITCH_MAX - PITCH_MIN + 1) * ROW,
    MIN_PPS = 46,
    MAX_PPS = 1472,
    ZOOM_STEP = 1.15,
    PIANO_MIN = 21,
    PIANO_MAX = 108,
    PIANO_HEIGHT = 72,
    BLACK_PITCHES = new Set([1, 3, 6, 8, 10]);
  var TRACK_COLORS = [
    "#1976d2",
    "#008b8b",
    "#3f8f3f",
    "#7559a6",
    "#b77900",
    "#b54f7d",
    "#087f8c",
    "#6d9628",
  ];
  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function button(text, title) {
    var n = el("button", "msr-btn", text);
    n.type = "button";
    if (title) n.title = title;
    return n;
  }
  function post(url, payload) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    }).then(function (r) {
      return r
        .json()
        .catch(function () {
          return {};
        })
        .then(function (v) {
          if (!r.ok) throw new Error(v.error || "HTTP " + r.status);
          return v;
        });
    });
  }
  function ResultSession(root) {
    this.root = root;
    this.host = root.querySelector(".msr-host");
    this.m = {};
    this.position = 0;
    this.playing = false;
    this.muted = new Set();
    this.solo = null;
    this.follow = true;
    this.raf = 0;
    this.audioStartPosition = 0;
    this.clockOrigin = 0;
    this.playbackGeneration = 0;
    this.mixPromise = Promise.resolve();
    // The synth plays the MIDI directly; the delay only covers its audio buffer.
    this.visualDelay = 0.05;
    try {
      var savedVisualDelay = localStorage.getItem(
        "midiWorkbenchVisualDelaySeconds",
      );
      if (savedVisualDelay !== null)
        this.visualDelay = clamp(parseFloat(savedVisualDelay) || 0, 0, 30);
    } catch (e) {}
    this.bpm = 120;
    this.firstBeatDelay = 0;
    this.gridStorageKey = "";
    this.pps = 92;
    this.drawRaf = 0;
    this.noteIndex = [];
    this.maxNoteDuration = 0;
    this.instrumentColors = {};
    this.chordInstruments = new Set();
    this.pianometerSignature = "";
    this.backgroundCanvas = null;
    this.viewportWidth = 950;
    this.maxScroll = 0;
    this.disposed = false;
    this.ownerId = "midi-result-" + nextSessionId++;
    this.onExternalPlayback = this.handleExternalPlayback.bind(this);
  }
  ResultSession.prototype.init = function () {
    try {
      this.m = JSON.parse(this.root.querySelector(".msr-manifest").textContent);
    } catch (e) {
      this.host.textContent = String(e);
      return;
    }
    var manifestBpm = parseFloat(this.m.bpm);
    if (Number.isFinite(manifestBpm)) this.bpm = clamp(manifestBpm, 20, 300);
    this.gridStorageKey = "midiGrid:" + this.m.downloads.midi;
    try {
      var savedGrid = JSON.parse(
        localStorage.getItem(this.gridStorageKey) || "null",
      );
      if (savedGrid) {
        var savedBpm = parseFloat(savedGrid.bpm),
          savedFirstBeat = parseFloat(savedGrid.firstBeatDelay);
        if (Number.isFinite(savedBpm)) this.bpm = clamp(savedBpm, 20, 300);
        if (Number.isFinite(savedFirstBeat))
          this.firstBeatDelay = clamp(savedFirstBeat, -60, 60);
      }
    } catch (e) {}
    this.noteIndex = this.m.notes.slice().sort(function (a, b) {
      return a.start - b.start;
    });
    this.maxNoteDuration = this.noteIndex.reduce(function (value, n) {
      return Math.max(value, n.end - n.start);
    }, 0);
    var self = this;
    this.m.instruments.forEach(function (i, index) {
      i.color = TRACK_COLORS[index % TRACK_COLORS.length];
      self.instrumentColors[i.id] = i.color;
      var midi = i.midi || [],
        chordSuitable =
          !midi.length ||
          midi.some(function (item) {
            var channel = Number(item.channel),
              program = Number(item.program);
            return channel !== 9 && (!Number.isFinite(program) || program < 112);
          });
      if (chordSuitable) self.chordInstruments.add(i.id);
    });
    this.build();
    window.addEventListener(
      "music-to-midi-playback-start",
      this.onExternalPlayback,
    );
    this.play.disabled = false;
    this.status.textContent = this.synthLabel() + " ready";
    this.drawStatic();
    this.layoutPlayhead();
  };
  ResultSession.prototype.build = function () {
    var self = this,
      s = this.m.strings;
    if (this.m.sourceTrackName) {
      this.host.appendChild(
        el(
          "div",
          "msr-source",
          s.linked_source
            .replace("{track}", this.m.sourceTrackName)
            .replace("{backend}", this.m.backendLabel),
        ),
      );
    }
    var bar = el("div", "msr-toolbar");
    this.play = button(s.play);
    this.play.disabled = true;
    this.play.onclick = function () {
      self.toggle();
    };
    bar.appendChild(this.play);
    var bpm = el("label", "msr-delay", "BPM");
    this.bpmInput = el("input");
    this.bpmInput.type = "number";
    this.bpmInput.min = "20";
    this.bpmInput.max = "300";
    this.bpmInput.step = ".1";
    this.bpmInput.value = String(this.bpm);
    this.bpmInput.setAttribute("aria-label", "BPM");
    this.bpmInput.oninput = function () {
      var value = parseFloat(this.value);
      if (!Number.isFinite(value)) return;
      self.bpm = clamp(value, 20, 300);
      self.saveGridSettings();
      self.scheduleDraw();
    };
    bpm.appendChild(this.bpmInput);
    bar.appendChild(bpm);
    var firstBeat = el("label", "msr-delay", "第一拍");
    this.firstBeatInput = el("input");
    this.firstBeatInput.type = "number";
    this.firstBeatInput.min = "-60";
    this.firstBeatInput.max = "60";
    this.firstBeatInput.step = ".01";
    this.firstBeatInput.value = String(this.firstBeatDelay);
    this.firstBeatInput.setAttribute("aria-label", "第一拍延遲秒數");
    this.firstBeatInput.oninput = function () {
      var value = parseFloat(this.value);
      if (!Number.isFinite(value)) return;
      self.firstBeatDelay = clamp(value, -60, 60);
      self.saveGridSettings();
      self.scheduleDraw();
    };
    firstBeat.appendChild(this.firstBeatInput);
    firstBeat.appendChild(document.createTextNode("秒"));
    bar.appendChild(firstBeat);
    var delay = el("label", "msr-delay", "畫面延遲");
    this.delayInput = el("input");
    this.delayInput.type = "number";
    this.delayInput.min = "0";
    this.delayInput.max = "30";
    this.delayInput.step = ".1";
    this.delayInput.value = String(this.visualDelay);
    this.delayInput.setAttribute("aria-label", "畫面延遲秒數");
    this.delayInput.oninput = function () {
      var value = parseFloat(this.value);
      if (!Number.isFinite(value)) return;
      self.visualDelay = clamp(value, 0, 30);
      try {
        localStorage.setItem(
          "midiWorkbenchVisualDelaySeconds",
          String(self.visualDelay),
        );
      } catch (e) {}
    };
    delay.appendChild(this.delayInput);
    delay.appendChild(document.createTextNode("秒"));
    bar.appendChild(delay);
    var follow = button(s.follow);
    follow.classList.add("active");
    follow.onclick = function () {
      self.follow = !self.follow;
      follow.classList.toggle("active", self.follow);
    };
    bar.appendChild(follow);
    var zoomOut = button("橫向 −", "橫向縮小"),
      zoomIn = button("橫向 +", "橫向放大");
    zoomOut.onclick = function () {
      self.zoomHorizontal(1 / ZOOM_STEP / ZOOM_STEP);
    };
    zoomIn.onclick = function () {
      self.zoomHorizontal(ZOOM_STEP * ZOOM_STEP);
    };
    bar.appendChild(zoomOut);
    bar.appendChild(zoomIn);
    this.clock = el("span", "msr-clock", "0.0s");
    bar.appendChild(this.clock);
    this.status = el("span", "msr-status", "");
    bar.appendChild(this.status);
    this.host.appendChild(bar);
    var grid = el("div", "msr-grid"),
      scroll = el("div", "msr-roll-scroll"),
      world = el("div", "msr-roll-world"),
      viewport = el("div", "msr-roll-viewport");
    this.canvas = el("canvas", "msr-roll");
    this.playhead = el("div", "msr-playhead");
    viewport.appendChild(this.canvas);
    viewport.appendChild(this.playhead);
    world.appendChild(viewport);
    scroll.appendChild(world);
    this.scroll = scroll;
    this.world = world;
    this.viewport = viewport;
    this.canvas.onclick = function (e) {
      var r = self.canvas.getBoundingClientRect();
      self.seek(
        (self.scroll.scrollLeft + e.clientX - r.left - LEFT) / self.pps,
      );
    };
    scroll.addEventListener(
      "scroll",
      function () {
        self.scheduleDraw();
        self.layoutPlayhead();
      },
      { passive: true },
    );
    scroll.addEventListener(
      "wheel",
      function (e) {
        self.onWheel(e);
      },
      { passive: false },
    );
    scroll.title = s.zoom_help;
    grid.appendChild(scroll);
    var aside = el("aside", "msr-instruments");
    aside.appendChild(el("h3", "", s.instruments));
    this.m.instruments.forEach(function (i, index) {
      var row = el("div", "msr-row" + (i.detected ? "" : " undetected"));
      row.dataset.instrument = i.id;
      row.title = i.id;
      var sw = el("span", "msr-swatch");
      sw.style.background = i.detected ? i.color : "#4b5157";
      row.appendChild(sw);
      var info = el("div", "msr-instrument-info"),
        name = el("span", "msr-name", i.label);
      name.title = i.id;
      info.appendChild(name);
      var midi = (i.midi || [])
        .map(function (item) {
          var channel = Number(item.channel) + 1;
          if (channel === 10) return "Ch 10 · Drum Kit";
          return (
            "Ch " +
            channel +
            " · Program " +
            (Number(item.program) + 1) +
            " " +
            item.program_name
          );
        })
        .join(" | ");
      if (midi) info.appendChild(el("div", "msr-meta", midi));
      row.appendChild(info);
      if (!i.detected) {
        row.appendChild(el("small", "", s.not_detected));
      } else {
        var solo = button("S", s.solo),
          mute = button("M", s.mute);
        solo.onclick = function () {
          self.toggleSolo(i.id);
        };
        mute.onclick = function () {
          self.toggleMute(i.id);
        };
        row.appendChild(solo);
        row.appendChild(mute);
        i.row = row;
        i.soloButton = solo;
        i.muteButton = mute;
      }
      aside.appendChild(row);
    });
    grid.appendChild(aside);
    this.host.appendChild(grid);
    var pianometer = el("div", "msr-pianometer"),
      pianometerMain = el("div", "msr-pianometer-main");
    this.chord = el("div", "msr-chord", "");
    this.chord.setAttribute("aria-live", "polite");
    this.keyboardCanvas = el("canvas", "msr-keyboard");
    this.keyboardCanvas.setAttribute("aria-label", "88-key piano keyboard");
    pianometerMain.appendChild(this.chord);
    pianometerMain.appendChild(this.keyboardCanvas);
    pianometer.appendChild(pianometerMain);
    this.pianometerMain = pianometerMain;
    this.host.appendChild(pianometer);
    this.resizeObserver = new ResizeObserver(function () {
      self.layout();
    });
    this.resizeObserver.observe(scroll);
    this.layout();
  };
  ResultSession.prototype.saveGridSettings = function () {
    try {
      localStorage.setItem(
        this.gridStorageKey,
        JSON.stringify({ bpm: this.bpm, firstBeatDelay: this.firstBeatDelay }),
      );
    } catch (e) {}
  };
  ResultSession.prototype.synthLabel = function () {
    return this.m.backendLabel || "MIDI output";
  };
  ResultSession.prototype.start = function () {
    var self = this,
      generation = ++this.playbackGeneration;
    if (this.position >= this.m.duration) this.position = 0;
    this.audioStartPosition = this.position;
    this.playing = true;
    this.play.textContent = this.m.strings.pause;
    window.dispatchEvent(
      new CustomEvent("music-to-midi-playback-start", {
        detail: { owner: this.ownerId, synth: true },
      }),
    );
    return post("/api/synth/play", {
      start_seconds: this.position,
    })
      .then(function () {
        if (self.disposed || generation !== self.playbackGeneration) return;
        self.clockOrigin = performance.now() / 1000;
        self.syncSynthMix().catch(function () {});
        self.status.textContent = self.synthLabel() + " playing";
        self.tick();
      })
      .catch(function (e) {
        if (generation !== self.playbackGeneration) return;
        self.playing = false;
        self.play.textContent = self.m.strings.play;
        self.status.textContent = String(e);
      });
  };
  ResultSession.prototype.pause = function (stopServer) {
    if (stopServer === undefined) stopServer = true;
    ++this.playbackGeneration;
    var wasPlaying = this.playing;
    this.playing = false;
    this.play.textContent = this.m.strings.play;
    cancelAnimationFrame(this.raf);
    this.layoutPlayhead();
    return wasPlaying && stopServer
      ? post("/api/synth/stop", {}).catch(function () {})
      : Promise.resolve();
  };
  ResultSession.prototype.toggle = function () {
    if (this.playing) this.pause();
    else this.start();
  };
  ResultSession.prototype.seek = function (seconds) {
    var self = this,
      was = this.playing,
      target = clamp(seconds, 0, this.m.duration);
    if (!was) {
      this.position = target;
      this.layoutPlayhead();
      return;
    }
    var stopped = this.pause(),
      generation = this.playbackGeneration;
    stopped.then(function () {
      if (generation !== self.playbackGeneration) return;
      self.position = target;
      self.start();
    });
  };
  ResultSession.prototype.handleExternalPlayback = function (e) {
    if (e.detail && e.detail.owner !== this.ownerId)
      this.pause(!e.detail.synth);
  };
  ResultSession.prototype.audible = function (id) {
    return !this.muted.has(id);
  };
  ResultSession.prototype.syncSynthMix = function () {
    var self = this;
    this.mixPromise = this.mixPromise
      .catch(function () {})
      .then(function () {
        return post("/api/synth/mix", {
          solo: self.solo,
          muted_tracks: Array.from(self.muted),
        });
      });
    return this.mixPromise;
  };
  ResultSession.prototype.toggleMute = function (id) {
    this.solo = null;
    if (this.muted.has(id)) this.muted.delete(id);
    else this.muted.add(id);
    this.syncRows();
    this.syncSynthMix().catch(function () {});
  };
  ResultSession.prototype.toggleSolo = function (id) {
    if (this.solo === id) {
      this.solo = null;
      this.muted.clear();
    } else {
      this.solo = id;
      this.muted = new Set(
        this.m.instruments
          .filter(function (i) {
            return i.detected && i.id !== id;
          })
          .map(function (i) {
            return i.id;
          }),
      );
    }
    this.syncRows();
    this.syncSynthMix().catch(function () {});
  };
  ResultSession.prototype.syncRows = function () {
    var self = this;
    this.m.instruments.forEach(function (i) {
      if (!i.detected) return;
      var muted = self.muted.has(i.id);
      i.row.classList.toggle("muted", muted);
      i.soloButton.classList.toggle("active", self.solo === i.id);
      i.muteButton.classList.toggle("active", muted);
      i.muteButton.textContent = "M";
    });
    this.drawStatic();
    this.updatePianometer();
  };
  ResultSession.prototype.tick = function () {
    if (!this.playing) return;
    this.position = Math.min(
      this.m.duration,
      this.audioStartPosition +
        Math.max(
          0,
          performance.now() / 1000 - this.clockOrigin - this.visualDelay,
        ),
    );
    if (this.position >= this.m.duration) {
      this.position = this.m.duration;
      this.pause();
      return;
    }
    if (this.follow) {
      var target = LEFT + this.position * this.pps - this.viewportWidth / 2;
      this.scroll.scrollLeft = clamp(target, 0, this.maxScroll);
    }
    this.layoutPlayhead();
    var self = this;
    this.raf = requestAnimationFrame(function () {
      self.tick();
    });
  };
  ResultSession.prototype.layout = function () {
    var width = Math.max(320, this.scroll.clientWidth || 950),
      dpr = Math.min(2, window.devicePixelRatio || 1),
      worldWidth = Math.max(width, LEFT + this.m.duration * this.pps + 80);
    this.viewportWidth = width;
    this.maxScroll = Math.max(0, worldWidth - width);
    this.viewport.style.width = width + "px";
    this.viewport.style.height = HEIGHT + "px";
    this.world.style.width = worldWidth + "px";
    this.world.style.height = HEIGHT + "px";
    this.canvas.style.width = width + "px";
    this.canvas.style.height = HEIGHT + "px";
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(HEIGHT * dpr);
    this.dpr = dpr;
    this.rebuildBackground();
    this.drawStatic();
    this.layoutPlayhead();
    this.layoutPianometer();
    if (!this.verticalPositionInitialized) {
      this.verticalPositionInitialized = true;
      var pitches = this.noteIndex
        .map(function (note) {
          return note.pitch;
        })
        .filter(function (pitch) {
          return pitch >= PITCH_MIN && pitch <= PITCH_MAX;
        })
        .sort(function (a, b) {
          return a - b;
        });
      var focusPitch = pitches.length
          ? pitches[Math.floor(pitches.length / 2)]
          : 60,
        focusY = HEADER + (PITCH_MAX - focusPitch + 0.5) * ROW;
      this.scroll.scrollTop = Math.max(
        0,
        focusY - this.scroll.clientHeight / 2,
      );
    }
  };
  ResultSession.prototype.scheduleDraw = function () {
    var self = this;
    if (this.drawRaf) return;
    this.drawRaf = requestAnimationFrame(function () {
      self.drawRaf = 0;
      self.drawStatic();
    });
  };
  ResultSession.prototype.findNoteStart = function (time) {
    var notes = this.noteIndex,
      lo = 0,
      hi = notes.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (notes[mid].start < time) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
  ResultSession.prototype.rebuildBackground = function () {
    var background = document.createElement("canvas"),
      d = this.dpr || 1,
      w = this.canvas.width / d;
    background.width = this.canvas.width;
    background.height = this.canvas.height;
    var p = background.getContext("2d");
    p.setTransform(d, 0, 0, d, 0, 0);
    p.fillStyle = "#fff";
    p.fillRect(0, 0, w, HEIGHT);
    p.fillStyle = "#e7e7e7";
    p.fillRect(0, 0, LEFT, HEADER);
    p.fillStyle = "#f4f4f4";
    p.fillRect(LEFT, 0, w - LEFT, HEADER);
    var blackKey = p.createLinearGradient(0, 0, 50, 0);
    blackKey.addColorStop(0, "#202020");
    blackKey.addColorStop(0.72, "#5d5d5d");
    blackKey.addColorStop(1, "#1b1b1b");
    p.beginPath();
    for (var pitch = PITCH_MIN; pitch <= PITCH_MAX; pitch++) {
      var y = HEADER + (PITCH_MAX - pitch) * ROW,
        black = BLACK_PITCHES.has(pitch % 12);
      p.fillStyle = black ? "#edf5ff" : "#fff";
      p.fillRect(LEFT, y, w - LEFT, ROW);
      p.fillStyle = "#fff";
      p.fillRect(0, y, LEFT, ROW);
      if (black) {
        p.fillStyle = blackKey;
        p.fillRect(0, y, 50, ROW - 1);
        p.fillStyle = "rgba(255,255,255,.24)";
        p.fillRect(1, y + 1, 46, 1);
      }
      p.moveTo(LEFT, y + 0.5);
      p.lineTo(w, y + 0.5);
      if (pitch % 12 === 0) {
        p.fillStyle = "#333";
        p.font = "9px 'MS UI Gothic',monospace";
        p.fillText("C" + (Math.floor(pitch / 12) - 1), LEFT - 20, y + 9);
      }
    }
    p.strokeStyle = "#bfd2eb";
    p.lineWidth = 1;
    p.stroke();
    p.beginPath();
    for (var keyPitch = PITCH_MIN; keyPitch <= PITCH_MAX; keyPitch++) {
      var keyY = HEADER + (PITCH_MAX - keyPitch) * ROW + 0.5;
      p.moveTo(0, keyY);
      p.lineTo(LEFT, keyY);
    }
    p.strokeStyle = "#7a7a7a";
    p.stroke();
    p.beginPath();
    for (var octavePitch = 0; octavePitch <= PITCH_MAX; octavePitch += 12) {
      var octaveY = HEADER + (PITCH_MAX - octavePitch) * ROW + 0.5;
      p.moveTo(LEFT, octaveY);
      p.lineTo(w, octaveY);
    }
    p.strokeStyle = "#404040";
    p.stroke();
    p.beginPath();
    p.moveTo(0, HEADER - 0.5);
    p.lineTo(w, HEADER - 0.5);
    p.moveTo(LEFT - 0.5, 0);
    p.lineTo(LEFT - 0.5, HEIGHT);
    p.strokeStyle = "#606060";
    p.stroke();
    this.backgroundCanvas = background;
  };
  ResultSession.prototype.zoomHorizontal = function (factor, anchorX) {
    if (anchorX === undefined) {
      // Buttons keep the playhead in place when it is on screen, else the view center.
      var playheadX = LEFT + this.position * this.pps - this.scroll.scrollLeft;
      anchorX =
        playheadX >= LEFT && playheadX <= this.viewportWidth
          ? playheadX
          : (LEFT + this.viewportWidth) / 2;
    }
    var anchorTime = (this.scroll.scrollLeft + anchorX - LEFT) / this.pps;
    this.pps = clamp(this.pps * factor, MIN_PPS, MAX_PPS);
    var worldWidth = Math.max(
      this.viewportWidth,
      LEFT + this.m.duration * this.pps + 80,
    );
    this.world.style.width = worldWidth + "px";
    this.maxScroll = Math.max(0, worldWidth - this.viewportWidth);
    this.scroll.scrollLeft = Math.max(
      0,
      LEFT + anchorTime * this.pps - anchorX,
    );
    this.drawStatic();
    this.layoutPlayhead();
  };
  ResultSession.prototype.onWheel = function (e) {
    var modifier = e.ctrlKey || e.altKey;
    if (modifier) {
      e.preventDefault();
      var rect = this.scroll.getBoundingClientRect();
      this.zoomHorizontal(
        e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP,
        clamp(e.clientX - rect.left, 0, this.viewportWidth),
      );
      return;
    }
    if (e.shiftKey) {
      e.preventDefault();
      this.scroll.scrollLeft += e.deltaY || e.deltaX;
    }
  };
  ResultSession.prototype.drawStatic = function () {
    if (!this.canvas || !this.backgroundCanvas) return;
    var p = this.canvas.getContext("2d"),
      d = this.dpr || 1,
      w = this.canvas.width / d,
      h = HEIGHT,
      scrollX = this.scroll.scrollLeft,
      start = Math.max(0, (scrollX - LEFT) / this.pps),
      end = Math.min(this.m.duration, (scrollX + w - LEFT) / this.pps);
    p.setTransform(1, 0, 0, 1, 0, 0);
    p.drawImage(this.backgroundCanvas, 0, 0);
    p.setTransform(d, 0, 0, d, 0, 0);
    p.save();
    p.beginPath();
    p.rect(LEFT, 0, w - LEFT, h);
    p.clip();
    var beatSeconds = 60 / this.bpm,
      subdivision = beatSeconds / 4,
      firstSubdivision = Math.floor(
        (start - this.firstBeatDelay) / subdivision,
      ),
      lastSubdivision = Math.ceil((end - this.firstBeatDelay) / subdivision);
    p.beginPath();
    for (
      var subdivisionIndex = firstSubdivision;
      subdivisionIndex <= lastSubdivision;
      subdivisionIndex++
    ) {
      if (((subdivisionIndex % 4) + 4) % 4 === 0) continue;
      var subdivisionTime =
          this.firstBeatDelay + subdivisionIndex * subdivision,
        subdivisionX =
          Math.round(LEFT + subdivisionTime * this.pps - scrollX) + 0.5;
      p.moveTo(subdivisionX, HEADER);
      p.lineTo(subdivisionX, h);
    }
    p.strokeStyle = "#b8cce8";
    p.lineWidth = 1;
    p.setLineDash([1, 3]);
    p.stroke();
    p.setLineDash([]);
    var firstBeatIndex = Math.floor(
        (start - this.firstBeatDelay) / beatSeconds,
      ),
      lastBeatIndex = Math.ceil((end - this.firstBeatDelay) / beatSeconds);
    p.beginPath();
    for (
      var beatIndex = firstBeatIndex;
      beatIndex <= lastBeatIndex;
      beatIndex++
    ) {
      if (((beatIndex % 4) + 4) % 4 === 0) continue;
      var beatTime = this.firstBeatDelay + beatIndex * beatSeconds,
        beatX = Math.round(LEFT + beatTime * this.pps - scrollX) + 0.5;
      p.moveTo(beatX, HEADER);
      p.lineTo(beatX, h);
    }
    p.strokeStyle = "#94b7e8";
    p.stroke();
    p.beginPath();
    for (
      var measureBeat = firstBeatIndex;
      measureBeat <= lastBeatIndex;
      measureBeat++
    ) {
      if (((measureBeat % 4) + 4) % 4 !== 0) continue;
      var measureTime = this.firstBeatDelay + measureBeat * beatSeconds,
        measureX = Math.round(LEFT + measureTime * this.pps - scrollX) + 0.5;
      p.moveTo(measureX, 0);
      p.lineTo(measureX, h);
    }
    p.strokeStyle = "#2764df";
    p.stroke();
    p.fillStyle = "#333";
    p.font = "10px Arial,'MS UI Gothic',sans-serif";
    for (
      var labelBeat = firstBeatIndex;
      labelBeat <= lastBeatIndex;
      labelBeat++
    ) {
      if (((labelBeat % 4) + 4) % 4 !== 0) continue;
      var labelTime = this.firstBeatDelay + labelBeat * beatSeconds,
        labelX = LEFT + labelTime * this.pps - scrollX,
        measureNumber = Math.floor(labelBeat / 4) + 1;
      p.fillText(String(measureNumber), labelX + 3, 12);
    }
    var firstNote = this.findNoteStart(
        Math.max(0, start - this.maxNoteDuration),
      ),
      lastNote = this.findNoteStart(end + 1e-9);
    for (var index = firstNote; index < lastNote; index++) {
      var n = this.noteIndex[index];
      if (n.pitch < PITCH_MIN || n.pitch > PITCH_MAX || n.end < start) continue;
      var noteX = LEFT + n.start * this.pps - scrollX,
        noteY = HEADER + (PITCH_MAX - n.pitch) * ROW + 1,
        width = Math.max(2, (n.end - n.start) * this.pps);
      p.globalAlpha = this.muted.has(n.instrument) ? 0.16 : 1;
      p.fillStyle = this.instrumentColors[n.instrument] || "#6f9fd8";
      p.fillRect(noteX, noteY, width, ROW - 2);
      p.strokeStyle = "rgba(0,45,105,.55)";
      p.strokeRect(noteX + 0.5, noteY + 0.5, Math.max(1, width - 1), ROW - 3);
    }
    p.globalAlpha = 1;
    p.restore();
  };
  ResultSession.prototype.layoutPlayhead = function () {
    if (!this.playhead) return;
    var x = LEFT + this.position * this.pps - this.scroll.scrollLeft;
    this.playhead.style.transform = "translate3d(" + x.toFixed(2) + "px,0,0)";
    this.playhead.style.visibility =
      x >= LEFT && x <= this.viewportWidth ? "visible" : "hidden";
    this.clock.textContent = this.position.toFixed(1) + "s";
    this.updatePianometer();
  };
  ResultSession.prototype.layoutPianometer = function () {
    if (!this.keyboardCanvas || !this.pianometerMain) return;
    var width = Math.max(320, this.pianometerMain.clientWidth || 950),
      dpr = Math.min(2, window.devicePixelRatio || 1);
    this.keyboardCanvas.style.width = width + "px";
    this.keyboardCanvas.style.height = PIANO_HEIGHT + "px";
    this.keyboardCanvas.width = Math.round(width * dpr);
    this.keyboardCanvas.height = Math.round(PIANO_HEIGHT * dpr);
    this.keyboardDpr = dpr;
    this.pianometerSignature = "";
    this.updatePianometer();
  };
  ResultSession.prototype.activePianoNotes = function () {
    var end = this.findNoteStart(this.position + 1e-9),
      start = this.findNoteStart(
        Math.max(0, this.position - this.maxNoteDuration - 1e-9),
      ),
      active = [];
    for (var index = start; index < end; index++) {
      var note = this.noteIndex[index];
      if (
        note.start <= this.position &&
        note.end > this.position &&
        note.pitch >= PIANO_MIN &&
        note.pitch <= PIANO_MAX &&
        this.audible(note.instrument)
      )
        active.push(note);
    }
    return active;
  };
  ResultSession.prototype.updatePianometer = function () {
    if (!this.keyboardCanvas) return;
    var self = this,
      active = this.activePianoNotes(),
      colorsByPitch = new Map();
    active.forEach(function (note) {
      var colors = colorsByPitch.get(note.pitch) || [],
        color = self.instrumentColors[note.instrument] || "#6f9fd8";
      if (colors.indexOf(color) < 0) colors.push(color);
      colorsByPitch.set(note.pitch, colors);
    });
    var chordPitches = Array.from(
        new Set(
          active
            .filter(function (note) {
              return self.chordInstruments.has(note.instrument);
            })
            .map(function (note) {
              return note.pitch;
            }),
        ),
      ).sort(function (a, b) {
        return a - b;
      }),
      names = chordPitches.map(function (pitch) {
        var names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
        return names[pitch % 12] + (Math.floor(pitch / 12) - 1);
      }),
      detected =
        names.length && window.Tonal
          ? window.Tonal.Chord.detect(names, { assumePerfectFifth: true })
          : [],
      chord = detected.length ? detected[0] : "",
      signature =
        Array.from(colorsByPitch.entries())
          .map(function (entry) {
            return entry[0] + ":" + entry[1].join(",");
          })
          .join("|") +
        ";" +
        chord;
    if (signature === this.pianometerSignature) return;
    this.pianometerSignature = signature;
    this.chord.textContent = chord;
    this.drawPianoKeyboard(colorsByPitch);
  };
  ResultSession.prototype.drawPianoKeyboard = function (colorsByPitch) {
    var p = this.keyboardCanvas.getContext("2d"),
      d = this.keyboardDpr || 1,
      width = this.keyboardCanvas.width / d,
      whiteWidth = width / 52,
      blackWidth = Math.max(3, whiteWidth * 0.62),
      blackHeight = PIANO_HEIGHT * 0.62,
      whiteIndex = 0,
      whitePositions = {};
    p.setTransform(d, 0, 0, d, 0, 0);
    p.clearRect(0, 0, width, PIANO_HEIGHT);
    for (var pitch = PIANO_MIN; pitch <= PIANO_MAX; pitch++) {
      if (BLACK_PITCHES.has(pitch % 12)) continue;
      var x = whiteIndex * whiteWidth,
        colors = colorsByPitch.get(pitch) || [];
      whitePositions[pitch] = x;
      p.fillStyle = colors.length ? colors[0] : "#fafafa";
      p.fillRect(x, 0, whiteWidth, PIANO_HEIGHT);
      if (colors.length > 1) {
        var stripeWidth = whiteWidth / colors.length;
        colors.forEach(function (color, index) {
          p.fillStyle = color;
          p.fillRect(x + index * stripeWidth, 0, stripeWidth, PIANO_HEIGHT);
        });
      }
      p.strokeStyle = "#7a8791";
      p.lineWidth = 1;
      p.strokeRect(x + 0.5, 0.5, Math.max(0, whiteWidth - 1), PIANO_HEIGHT - 1);
      whiteIndex++;
    }
    for (var blackPitch = PIANO_MIN; blackPitch <= PIANO_MAX; blackPitch++) {
      if (!BLACK_PITCHES.has(blackPitch % 12)) continue;
      var previous = blackPitch - 1;
      while (BLACK_PITCHES.has(previous % 12)) previous--;
      var blackX = whitePositions[previous] + whiteWidth - blackWidth / 2,
        blackColors = colorsByPitch.get(blackPitch) || [];
      p.fillStyle = blackColors.length ? blackColors[0] : "#20262b";
      p.fillRect(blackX, 0, blackWidth, blackHeight);
      if (blackColors.length > 1) {
        var blackStripeWidth = blackWidth / blackColors.length;
        blackColors.forEach(function (color, index) {
          p.fillStyle = color;
          p.fillRect(
            blackX + index * blackStripeWidth,
            0,
            blackStripeWidth,
            blackHeight,
          );
        });
      }
      p.strokeStyle = "#111";
      p.strokeRect(blackX + 0.5, 0.5, blackWidth - 1, blackHeight - 1);
    }
  };
  ResultSession.prototype.dispose = function () {
    if (this.disposed) return;
    this.disposed = true;
    this.pause();
    window.removeEventListener(
      "music-to-midi-playback-start",
      this.onExternalPlayback,
    );
    if (this.resizeObserver) this.resizeObserver.disconnect();
    cancelAnimationFrame(this.drawRaf);
  };
  function scan() {
    for (var i = sessions.length - 1; i >= 0; i--) {
      if (!sessions[i].root.isConnected) {
        sessions[i].dispose();
        sessions.splice(i, 1);
      }
    }
    document
      .querySelectorAll(".msr-root:not([data-msr-init])")
      .forEach(function (root) {
        if (!root.querySelector(".msr-host")) return;
        root.setAttribute("data-msr-init", "1");
        var s = new ResultSession(root);
        sessions.push(s);
        s.init();
      });
  }
  var timer = 0;
  function schedule() {
    if (timer) return;
    timer = setTimeout(function () {
      timer = 0;
      scan();
    }, 40);
  }
  new MutationObserver(function (changes) {
    for (var i = 0; i < changes.length; i++) {
      for (var j = 0; j < changes[i].addedNodes.length; j++) {
        var n = changes[i].addedNodes[j];
        if (
          n.nodeType === 1 &&
          (n.matches(".msr-root") || n.querySelector(".msr-root"))
        ) {
          schedule();
          return;
        }
      }
      for (var k = 0; k < changes[i].removedNodes.length; k++) {
        var r = changes[i].removedNodes[k];
        if (
          r.nodeType === 1 &&
          (r.matches(".msr-root") || r.querySelector(".msr-root"))
        ) {
          schedule();
          return;
        }
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", schedule);
  else schedule();
})();
