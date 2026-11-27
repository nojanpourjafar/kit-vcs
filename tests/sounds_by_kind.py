"""Generated drum sounds of each kind, with random variation, for testing the sorting by kind."""
import math, random
R = 44100
def lowpass(xs, a):   # one-pole: a near 1 keeps only lows
    out, y = [], 0.0
    for x in xs: y = a * y + (1 - a) * x; out.append(y)
    return out
def highpass(xs, a):
    lp = lowpass(xs, a); return [x - l for x, l in zip(xs, lp)]
def norm(xs, peak=0.9):
    top = max(map(abs, xs)) or 1; return [x / top * peak for x in xs]
def kick(r):
    f, dec, sw, n = r.uniform(42, 65), r.uniform(0.08, 0.35), r.uniform(60, 220), r.uniform(0.3, 0.8)
    click = r.uniform(0, 0.3)
    xs = [math.sin(2*math.pi*(f*i/R + sw*0.03*(1-math.exp(-i/R/0.03)))) * math.exp(-i/R/dec) for i in range(int(n*R))]
    for i in range(int(0.004*R)): xs[i] += click * r.uniform(-1, 1)
    return norm(xs)
def eight08(r):
    f, dec, n, drive = r.uniform(35, 70), r.uniform(0.5, 1.5), r.uniform(1.0, 3.0), r.choice([0, 0.5, 1.5])
    ph, xs = 0.0, []
    for i in range(int(n*R)):
        t = i/R; ph += 2*math.pi*f*(1 + 0.6*math.exp(-t/0.015))/R
        x = math.sin(ph) * math.exp(-t/dec); xs.append(math.tanh(x*(1+3*drive)) if drive else x)
    return norm(xs)
def snare(r):
    body, dec, ndec, n = r.uniform(160, 260), r.uniform(0.05, 0.15), r.uniform(0.08, 0.25), r.uniform(0.25, 0.5)
    noise = lowpass([r.uniform(-1, 1) for _ in range(int(n*R))], r.uniform(0.3, 0.6))
    return norm([0.5*math.sin(2*math.pi*body*i/R)*math.exp(-i/R/dec) + 0.8*noise[i]*math.exp(-i/R/ndec) for i in range(int(n*R))])
def clap(r):
    n = r.uniform(0.25, 0.6); bursts = r.randint(3, 5); gap = r.uniform(0.006, 0.012)
    raw = highpass(lowpass([r.uniform(-1, 1) for _ in range(int(n*R))], 0.4), 0.95)  # band-limited noise
    xs = [0.0] * len(raw)
    for i in range(len(raw)):
        t = i / R; env = 0.0
        for b in range(bursts):
            s = b * gap
            if t >= s: env = max(env, math.exp(-(t - s) / (0.004 if b < bursts - 1 else r.uniform(0.08, 0.2))))
        xs[i] = raw[i] * env
    return norm(xs)
def hat(r, open_=False):
    n = r.uniform(0.3, 0.8) if open_ else r.uniform(0.05, 0.2); dec = r.uniform(0.1, 0.3) if open_ else r.uniform(0.01, 0.05)
    raw = highpass(highpass([r.uniform(-1, 1) for _ in range(int(n*R))], 0.7), 0.7)
    return norm([x * math.exp(-i/R/dec) for i, x in enumerate(raw)])
def perc(r):   # wood block, rim, conga-ish: a short pitched knock
    f, dec = r.uniform(300, 1200), r.uniform(0.015, 0.08)
    return norm([(math.sin(2*math.pi*f*i/R) + 0.4*math.sin(2*math.pi*f*2.7*i/R)) * math.exp(-i/R/dec) for i in range(int(0.25*R))])
def melodic(r):
    m = r.randint(48, 80); f = 440*2**((m-69)/12); h = r.choice([1, 3, 8]); n = r.uniform(0.6, 2.0)
    return norm([sum(math.sin(2*math.pi*f*k*i/R)/k for k in range(1, h+1)) * min(1, i/R/0.005) * math.exp(-i/R/0.5) for i in range(int(n*R))])
KINDS = {"Kick": kick, "808": eight08, "Snare": snare, "Clap": clap, "Hat": lambda r: hat(r, r.random() < 0.3), "Perc": perc, "Melodic": melodic}
