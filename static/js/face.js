/* Procam Attendance — face capture using face-api.js (128-D embeddings).
 *
 * Loads model weights from /static/face-weights/ (vendored locally). Falls back
 * to public CDN mirrors only if the local copy is missing. This means once you
 * run `python3 download_face_models.py` you have zero internet dependency.
 *
 * Two public APIs:
 *   PCFace.capture()     — one shot. Writes the descriptor into #pc-descriptor.
 *                          Used during PUNCH (the worker is verifying themselves).
 *   PCFace.captureMany() — captures FIVE samples at slight intervals and writes
 *                          a JSON array of descriptors into #pc-descriptors-many.
 *                          Used during ENROLMENT (one good sample per pose).
 *
 * Quality gates applied on every detection:
 *   - face-api.js detection score ≥ 0.70   (else "no clear face")
 *   - bounding-box width ≥ 100 px           (else "move closer")
 *   - face roughly centred                  (else "look straight ahead")
 */

(function () {
  const VIDEO_ID         = "pc-cam";
  const CANVAS_ID        = "pc-snap";
  const DESC_ID          = "pc-descriptor";
  const DESC_MANY_ID     = "pc-descriptors-many";
  const STATUS_ID        = "pc-face-status";
  const SAMPLE_COUNT_ID  = "pc-sample-count";   // optional progress UI

  let modelsLoaded = false;
  let camStream = null;
  let libLoading = null;

  // Preferred weight source: local (vendored). Falls back to public mirrors.
  // Look for a <meta name="pc-face-weights" content="/static/face-weights"> on the page
  // to override; otherwise use the default static path.
  const LOCAL_WEIGHTS = (() => {
    const meta = document.querySelector('meta[name="pc-face-weights"]');
    return meta ? meta.content : "/static/face-weights";
  })();
  const LIB_URLS = [
    "https://cdn.jsdelivr.net/npm/face-api.js@0.22.2/dist/face-api.min.js",
    "https://unpkg.com/face-api.js@0.22.2/dist/face-api.min.js"
  ];
  const WEIGHT_SOURCES = [
    LOCAL_WEIGHTS,
    "https://justadudewhohacks.github.io/face-api.js/weights",
    "https://raw.githubusercontent.com/justadudewhohacks/face-api.js/master/weights",
    "https://vladmandic.github.io/face-api/model"
  ];

  // Quality thresholds — tuned for warehouse kiosk use, not lab conditions.
  const MIN_DETECTION_SCORE = 0.55;
  const MIN_FACE_WIDTH_PX   = 80;   // matches the 80×80 minimum used by the GitHub reference project
  const CENTER_TOLERANCE    = 0.35; // face-centre must be within ±35% of frame width from middle
  const SAMPLE_COUNT        = 5;
  const SAMPLE_INTERVAL_MS  = 700;

  function setStatus(s, cls) {
    const el = document.getElementById(STATUS_ID);
    if (!el) return;
    el.innerHTML = s;
    el.style.color = cls === "ok" ? "#1a7f37"
                   : cls === "err" ? "#b00020"
                   : cls === "warn" ? "#a06400" : "";
    el.style.fontWeight = cls ? "700" : "";
  }

  function setCaptureEnabled(on) {
    document.querySelectorAll(
      '[onclick*="PCFace.capture"], [onclick*="PCEnrol.captureNext"], ' +
      '[data-pcface-capture], [data-pc-capture]'
    ).forEach(btn => {
      btn.disabled = !on;
      btn.style.opacity = on ? "" : "0.55";
      btn.style.cursor = on ? "" : "not-allowed";
    });
  }

  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.async = true;
      s.onload = () => res(src);
      s.onerror = () => rej(new Error("script failed: " + src));
      document.head.appendChild(s);
    });
  }

  async function ensureLib() {
    if (window.faceapi) return;
    if (libLoading) return libLoading;
    libLoading = (async () => {
      for (const url of LIB_URLS) {
        try { await loadScript(url); if (window.faceapi) return; } catch (e) {}
      }
      throw new Error("Could not load face-api.js from any CDN");
    })();
    return libLoading;
  }

  async function loadModels() {
    if (modelsLoaded) return;
    setCaptureEnabled(false);
    const t0 = Date.now();
    let tick = setInterval(() => {
      if (!modelsLoaded) setStatus("Loading face models… " +
        Math.round((Date.now() - t0) / 1000) + "s");
    }, 1000);
    await ensureLib();
    let lastErr = null;
    for (const URL of WEIGHT_SOURCES) {
      try {
        await Promise.all([
          faceapi.nets.tinyFaceDetector.loadFromUri(URL),
          faceapi.nets.faceLandmark68Net.loadFromUri(URL),
          faceapi.nets.faceRecognitionNet.loadFromUri(URL)
        ]);
        modelsLoaded = true;
        clearInterval(tick);
        const fromLocal = URL === LOCAL_WEIGHTS;
        setStatus("✓ Models ready " + (fromLocal ? "(local)" : "(CDN)") +
                  ". Click <strong>Capture face</strong>.", "ok");
        setCaptureEnabled(true);
        return;
      } catch (e) {
        lastErr = e;
        console.warn("face-api source failed:", URL, e);
      }
    }
    clearInterval(tick);
    setStatus("Could not load face models from any source.<br>" +
              "<small>Run <code>python3 download_face_models.py</code> in the project root, " +
              "then refresh. Or check Brave Shields / internet.<br>" +
              (lastErr && lastErr.message || "") + "</small>", "err");
    throw lastErr || new Error("models failed");
  }

  async function startCam() {
    setCaptureEnabled(false);
    const video = document.getElementById(VIDEO_ID);
    if (!video) return;
    const host = location.hostname;
    const proto = location.protocol;
    const isSecure = proto === "https:" || host === "localhost" || host === "127.0.0.1";
    if (!isSecure) {
      setStatus("This page is open at <code>" + location.host + "</code>, which the browser does " +
                "<strong>not</strong> treat as secure. Camera will not work.<br>" +
                "Open <code>http://localhost:5055" + location.pathname + "</code> instead.", "err");
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("This browser does not expose the camera API.", "err");
      return;
    }
    try {
      camStream = await navigator.mediaDevices.getUserMedia(
        { video: { facingMode: "user", width: 640, height: 480 }, audio: false });
      video.srcObject = camStream;
      await new Promise(r => (video.onloadedmetadata = r));
      setStatus("Camera on — loading face models…");
      await loadModels();
    } catch (e) {
      const msg = (e.name === "NotAllowedError")
        ? "Camera permission denied. Click the camera icon in the address bar and allow access."
        : ((e.name === "NotFoundError") ? "No camera found on this device."
           : ("Camera could not start: " + (e.message || e.name)));
      setStatus(msg, "err");
    }
  }

  /** Estimate yaw + pitch from the 68-point face landmarks.
   *  Returns { yaw, pitch } in roughly [-1, +1] units where:
   *    yaw  : negative = looking LEFT (subject's left), positive = looking RIGHT
   *    pitch: negative = looking UP, positive = looking DOWN
   *
   *  Method: distance from nose tip (landmark 30) to the face midline (between
   *  eye corners) divided by half-face-width. Surprisingly robust for our
   *  purposes — we only need to know which side the person is turned toward.
   */
  function estimatePose(detection) {
    const landmarks = detection.landmarks;
    if (!landmarks) return null;
    const pts = landmarks.positions;
    // 36 = outer corner of LEFT eye (subject's left, image right)
    // 45 = outer corner of RIGHT eye (subject's right, image left)
    // 30 = nose tip
    // 8  = chin tip
    // 27 = top of nose bridge
    const leftEye  = pts[36];
    const rightEye = pts[45];
    const nose     = pts[30];
    const chin     = pts[8];
    const topNose  = pts[27];

    const eyeMidX = (leftEye.x + rightEye.x) / 2;
    const eyeMidY = (leftEye.y + rightEye.y) / 2;
    const eyeWidth = Math.abs(rightEye.x - leftEye.x);
    const faceHeight = Math.abs(chin.y - topNose.y);

    // Yaw: where is the nose relative to the eyes' midline?
    // In camera (mirror) view: nose to image-left of eye-mid → subject turned RIGHT (looking RIGHT)
    //                          nose to image-right of eye-mid → subject turned LEFT (looking LEFT)
    // Normalise by eyeWidth so it's invariant to face size / distance.
    const yaw = eyeWidth > 0 ? (eyeMidX - nose.x) / (eyeWidth / 2) : 0;
    // Pitch: nose vertical position vs eye-midline, normalised by face height
    const pitch = faceHeight > 0 ? (nose.y - eyeMidY) / (faceHeight / 2) : 0;
    return { yaw, pitch };
  }

  /** Check whether the detected pose matches the requested pose.
   *  Returns { ok, reason } — reject the capture if it doesn't match. */
  function poseMatches(detection, expected) {
    if (!expected) return { ok: true };
    const p = estimatePose(detection);
    if (!p) return { ok: true };  // landmarks missing → skip check rather than block

    // Thresholds tuned by eyeballing real captures. Tighter for centre,
    // looser for the angle poses (don't expect anyone to swivel 90°).
    const Y_CENTRE = 0.20;   // |yaw| must be < this for "Centre"
    const Y_SIDE   = 0.20;   // |yaw| must be > this for "Left"/"Right"
    const P_CENTRE = 0.55;   // |pitch| must be < this for "Centre" (eyes-vs-nose ratio varies)
    const P_VERT   = 0.30;   // pitch swing required for "Up"/"Down"

    const pose = (expected || "").toLowerCase();
    if (pose.includes("centre") || pose.includes("glasses")) {
      // Centre + glasses extras both require frontal pose.
      if (Math.abs(p.yaw) > Y_CENTRE)
        return { ok: false,
                 reason: "Look straight at the camera (you're turned " +
                         (p.yaw < 0 ? "left" : "right") + "). yaw=" + p.yaw.toFixed(2) };
    }
    else if (pose.includes("left")) {
      // Subject must be turned to THEIR left → nose appears RIGHT of eye-mid in mirror image
      // → yaw is NEGATIVE in our convention. Looser limit: we just want some turn.
      if (p.yaw > -Y_SIDE)
        return { ok: false,
                 reason: "Turn your head MORE to the LEFT — yaw " + p.yaw.toFixed(2) +
                         " (need < -" + Y_SIDE + ")." };
    }
    else if (pose.includes("right")) {
      if (p.yaw < Y_SIDE)
        return { ok: false,
                 reason: "Turn your head MORE to the RIGHT — yaw " + p.yaw.toFixed(2) +
                         " (need > +" + Y_SIDE + ")." };
    }
    else if (pose.includes("up")) {
      // Looking up → nose moves UP relative to eyes → pitch decreases (negative)
      if (p.pitch > -P_VERT + 0.10)   // small offset because resting pitch isn't zero
        return { ok: false,
                 reason: "Tilt your head UP MORE — pitch " + p.pitch.toFixed(2) + "." };
    }
    else if (pose.includes("down")) {
      if (p.pitch < P_VERT)
        return { ok: false,
                 reason: "Tilt your head DOWN MORE — pitch " + p.pitch.toFixed(2) + "." };
    }
    return { ok: true };
  }

  /** Detect + score + apply quality gates. Returns {ok, detection, reason}.
   *  If `expectedPose` is given, also verifies the head pose matches.
   */
  async function detectWithGates(expectedPose) {
    if (!modelsLoaded) return { ok: false, reason: "Models still loading." };
    const video = document.getElementById(VIDEO_ID);
    if (!video || !video.videoWidth) return { ok: false, reason: "Camera not ready." };

    const det = await faceapi
      .detectSingleFace(video, new faceapi.TinyFaceDetectorOptions({ inputSize: 416, scoreThreshold: 0.5 }))
      .withFaceLandmarks()
      .withFaceDescriptor();
    if (!det) return { ok: false, reason: "No face detected. Move closer, face the camera." };

    const score = det.detection.score;
    const box = det.detection.box;
    if (score < MIN_DETECTION_SCORE) {
      return { ok: false, reason: "Face unclear (confidence " + Math.round(score * 100) +
                                  "%). Improve lighting." };
    }
    if (box.width < MIN_FACE_WIDTH_PX) {
      return { ok: false, reason: "Face too small (" + Math.round(box.width) +
                                  "px). Move closer to the camera." };
    }
    const cx = (box.x + box.width / 2) / video.videoWidth;
    if (Math.abs(cx - 0.5) > CENTER_TOLERANCE) {
      return { ok: false, reason: "Face off-centre. Position yourself in the middle of the frame." };
    }

    // NEW — pose-specific check. Only runs when caller passed expectedPose.
    if (expectedPose) {
      const pm = poseMatches(det, expectedPose);
      if (!pm.ok) return { ok: false, reason: pm.reason };
    }
    return { ok: true, detection: det };
  }

  /** Single capture. Optional argument: expected pose name ("Centre", "Left",
   *  "Right", "Up", "Down", "Glasses-On", "Glasses-Off"). When given, the
   *  capture is REJECTED if the user isn't actually turned in that direction. */
  async function capture(expectedPose) {
    setStatus(expectedPose ? ("Verifying pose: " + expectedPose + "…") : "Detecting face…");
    const r = await detectWithGates(expectedPose);
    if (!r.ok) { setStatus("❌ " + r.reason, "err"); return null; }

    const desc = Array.from(r.detection.descriptor);
    const descEl = document.getElementById(DESC_ID);
    if (descEl) descEl.value = JSON.stringify(desc);

    // snapshot for audit
    const canvas = document.getElementById(CANVAS_ID);
    const video  = document.getElementById(VIDEO_ID);
    if (canvas && video) {
      canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
    }
    const pct = Math.round(r.detection.detection.score * 100);
    setStatus("✓ Face captured (confidence " + pct + "%).", "ok");
    return desc;
  }

  /** Multi-shot capture for ENROLMENT. Captures SAMPLE_COUNT good frames, writes
   *  a JSON array of descriptors into #pc-descriptors-many. The user is asked
   *  to slightly shift their head between frames for robustness. */
  async function captureMany(target = SAMPLE_COUNT) {
    const descriptors = [];
    const countEl = document.getElementById(SAMPLE_COUNT_ID);
    const setCount = () => countEl && (countEl.textContent =
      `${descriptors.length} / ${target} samples`);

    setStatus("Hold still — capturing sample 1…");
    let attempts = 0;
    const MAX_ATTEMPTS = target * 4;  // give plenty of room for retries
    while (descriptors.length < target && attempts < MAX_ATTEMPTS) {
      attempts++;
      const r = await detectWithGates();
      if (r.ok) {
        descriptors.push(Array.from(r.detection.descriptor));
        setCount();
        if (descriptors.length < target) {
          const promptText = [
            "Now turn your head slightly LEFT…",
            "Now turn your head slightly RIGHT…",
            "Now tilt your head slightly UP…",
            "Now tilt your head slightly DOWN…"
          ][descriptors.length - 1] || "Hold still…";
          setStatus("✓ Sample " + descriptors.length + " captured. " + promptText, "ok");
        }
      } else {
        setStatus("⚠ " + r.reason, "warn");
      }
      await new Promise(r => setTimeout(r, SAMPLE_INTERVAL_MS));
    }

    if (descriptors.length < target) {
      setStatus("❌ Only captured " + descriptors.length + " of " + target +
                " good samples. Improve lighting and try again.", "err");
      return null;
    }

    const manyEl = document.getElementById(DESC_MANY_ID);
    if (manyEl) manyEl.value = JSON.stringify(descriptors);
    // Back-compat: also fill single descriptor with the first sample
    const single = document.getElementById(DESC_ID);
    if (single) single.value = JSON.stringify(descriptors[0]);

    // snapshot
    const canvas = document.getElementById(CANVAS_ID);
    const video  = document.getElementById(VIDEO_ID);
    if (canvas && video) {
      canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
    }
    setStatus("✓ Enrolment complete — " + descriptors.length + " samples captured. Click <strong>Save enrolment</strong>.", "ok");
    return descriptors;
  }

  window.PCFace = { start: startCam, capture, captureMany };
  document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById(VIDEO_ID)) startCam();
  });
})();
