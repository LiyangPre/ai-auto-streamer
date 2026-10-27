"""Render our original, seamless 48-second electric-piano lounge loop locally."""
from pathlib import Path
import numpy as np
import lameenc

RATE=44100
BEAT=.75
BAR=BEAT*4
LENGTH=16*BAR
audio=np.zeros((int(LENGTH*RATE),2),dtype=np.float64)

def note(start,midi,length,gain,pan=0):
    t=np.arange(int((length+1.8)*RATE))/RATE
    f=440*2**((midi-69)/12)
    attack=1-np.exp(-t/.018)
    release=np.exp(-np.maximum(0,t-length)/.55)
    env=attack*np.exp(-t/2.4)*release
    phase=2*np.pi*f*t
    tone=(np.sin(phase+.35*np.sin(2*phase)*np.exp(-t/.3))+.12*np.sin(2*phase)+.025*np.sin(3*phase))*env*gain
    gains=np.sqrt(np.array([1-pan,1+pan])/2)
    # Wrap each release into the start, keeping the loop boundary continuous.
    indices=(np.arange(len(tone))+int(start*RATE))%len(audio)
    audio[indices]+=tone[:,None]*gains

# Dm9 / G13 / Cmaj9 / Am9, gentle voicings and sparse improvised chord tones.
chords=[[53,60,64,69,72],[53,59,64,69,74],[52,59,62,67,71],[55,60,64,69,71]]
bass=[38,43,36,33]
for bar in range(16):
    chord=chords[bar%4]
    for i,pitch in enumerate(chord):note(bar*BAR+i*.035,pitch,1.5,.095,(i-2)*.16)
    for i in [1,3,4]:note(bar*BAR+BEAT*2.5+(i-1)*.027,chord[i],.65,.045,(i-2)*.18)
    note(bar*BAR,bass[bar%4],1.9,.12)
    if bar%2==1:
        note(bar*BAR+BEAT*1.5,chord[3]+12,.55,.027,.35)
        note(bar*BAR+BEAT*3.25,chord[2]+12,.45,.022,-.3)
# Quiet circular delay/reverb preserves the same seamless period.
dry=audio.copy()
for delay,gain in [(.19,.14),(.37,.09),(.61,.055)]:
    audio+=np.roll(dry,int(delay*RATE),axis=0)[:,::-1]*gain
audio=np.tanh(audio*1.3)
audio*=.72/np.max(np.abs(audio))
pcm=np.rint(audio*32767).astype('<i2').tobytes()
encoder=lameenc.Encoder();encoder.set_bit_rate(128);encoder.set_in_sample_rate(RATE)
encoder.set_channels(2);encoder.set_quality(2)
path=Path(__file__).resolve().parents[1]/'frontend/static/music/conversation-lounge.mp3'
path.parent.mkdir(parents=True,exist_ok=True)
path.write_bytes(encoder.encode(pcm)+encoder.flush())
print(f'{path.name}: {LENGTH:g}s, {path.stat().st_size} bytes, peak={np.max(np.abs(audio)):.2f}')
