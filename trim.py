"""trim.py - cuts the reference screenshot that YouTube Create sometimes adds at the END of the video.

trim_reference_end(video_path, reference_path) -> path of the video to use
  * looks at the last seconds of the video frame by frame
  * only if those frames show the reference picture (still, not moving) it cuts them off
  * if nothing like that is found, the video is NOT touched and the same path is returned
Try it alone:  python trim.py video.mp4 reference.png
"""
import io, os, subprocess, sys, tempfile

LOOK_BACK = 6.0        # seconds at the end that are checked
FPS = 4                # frames per second checked
MATCH = 0.70           # similarity (0..1) with the reference needed to call a frame "the reference screenshot"
MIN_KEEP = 3.0         # never cut if less than this many seconds would remain
THUMB = 48


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _duration(path):
    try:
        return float(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "default=nw=1:nk=1", path]).stdout.strip())
    except Exception:
        return 0.0


def _gray(im, size=(THUMB, THUMB)):
    return list(im.convert("L").resize(size).getdata())


def _corr(a, b):
    """Pearson correlation of two equally long lists (1 = same picture, 0 = unrelated)."""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va < 1e-6 or vb < 1e-6:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return max(0.0, cov / (va * vb) ** 0.5)


def _variants(frame, ref):
    """The reference can be shown 'fit' (black bars) or 'cover' (cropped to fill the frame): compare both ways."""
    from PIL import Image
    fw, fh = frame.size
    out = []
    # fit: reference scaled to the frame width, centred; compare only that band of the frame
    rh = max(1, int(ref.height * fw / ref.width))
    if rh <= fh:
        top = (fh - rh) // 2
        band = frame.crop((0, top, fw, top + rh))
        out.append(_corr(_gray(band), _gray(ref)))
    # cover: reference scaled to fill the frame, centre-cropped
    s = max(fw / ref.width, fh / ref.height)
    rw2, rh2 = max(1, int(ref.width * s)), max(1, int(ref.height * s))
    big = ref.resize((rw2, rh2))
    l, t = (rw2 - fw) // 2, (rh2 - fh) // 2
    out.append(_corr(_gray(frame), _gray(big.crop((l, t, l + fw, t + fh)))))
    return max(out) if out else 0.0


def _tail_frames(path, dur):
    """[(time, PIL image)] for the last LOOK_BACK seconds."""
    from PIL import Image
    start = max(0.0, dur - LOOK_BACK)
    tmp = tempfile.mkdtemp()
    _run(["ffmpeg", "-y", "-ss", "%.2f" % start, "-i", path, "-vf", "fps=%d,scale=360:-2" % FPS,
          os.path.join(tmp, "f_%04d.png")])
    files = sorted(f for f in os.listdir(tmp) if f.startswith("f_"))
    frames = []
    for i, f in enumerate(files):
        frames.append((start + i / FPS, Image.open(os.path.join(tmp, f)).convert("RGB")))
    return frames


def find_cut_time(path, reference_path, verbose=True):
    """Time (seconds) where the reference screenshot starts at the end of the video, or None."""
    from PIL import Image
    dur = _duration(path)
    if dur <= MIN_KEEP + 0.5:
        return None
    ref = Image.open(reference_path).convert("RGB")
    frames = _tail_frames(path, dur)
    if len(frames) < 3:
        return None
    sims = [(t, _variants(im, ref)) for t, im in frames]
    # walk backwards from the last frame while the frames still look like the reference
    cut = None
    for t, s in reversed(sims):
        if s >= MATCH:
            cut = t
        else:
            break
    if verbose:
        print("  [trim] video %.1fs, similarity of the last frames with the reference: %s"
              % (dur, " ".join("%.2f" % s for _, s in sims[-8:])))
    if cut is None:
        return None
    # the reference must also be a held still picture (frames inside it barely change)
    held = [im for t, im in frames if t >= cut]
    if len(held) >= 2:
        diffs = []
        base = _gray(held[0], (24, 24))
        for im in held[1:]:
            g = _gray(im, (24, 24))
            diffs.append(sum(abs(x - y) for x, y in zip(base, g)) / len(g))
        if max(diffs) > 12:                      # it moves: this is the real video, not a held screenshot
            return None
    cut = max(0.0, cut - 0.15)                   # small safety margin so no screenshot frame is left
    if cut < MIN_KEEP:
        if verbose:
            print("  [trim] cutting would leave under %.0fs, so the video is left as it is" % MIN_KEEP)
        return None
    return cut


def trim_reference_end(video_path, reference_path):
    """Returns the path of the video to use (a trimmed copy, or the original if nothing had to be cut)."""
    try:
        if not (video_path and os.path.exists(video_path) and reference_path and os.path.exists(reference_path)):
            return video_path
        cut = find_cut_time(video_path, reference_path)
        if cut is None:
            print("  [trim] no reference screenshot at the end of the video: nothing cut")
            return video_path
        base, ext = os.path.splitext(video_path)
        out = base + "_trim" + (ext or ".mp4")
        r = _run(["ffmpeg", "-y", "-i", video_path, "-t", "%.2f" % cut, "-c:v", "libx264", "-preset", "veryfast",
                  "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", out])
        if os.path.exists(out) and os.path.getsize(out) > 0 and _duration(out) > 0:
            print("  [trim] reference screenshot found at the end: cut at %.1fs -> %s" % (cut, out))
            return out
        print("  [trim] cutting failed, original video kept:", (r.stderr or "")[-150:])
    except Exception as e:
        print("  [trim] skipped:", str(e)[:120])
    return video_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Use: python trim.py video.mp4 reference.png")
        sys.exit(0)
    print("result:", trim_reference_end(sys.argv[1], sys.argv[2]))
