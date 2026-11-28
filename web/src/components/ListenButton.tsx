import { Square, Volume2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useLang } from '../lib/lang';
import { canSpeak, findVoice, speak, stopSpeaking } from '../lib/speak';

export function ListenButton({ text, className = 'btn btn-secondary btn-sm' }: { text: string; className?: string }) {
  const { bcp47, t } = useLang();
  const [playing, setPlaying] = useState(false);
  const [note, setNote] = useState('');

  useEffect(() => () => stopSpeaking(), []);
  useEffect(() => {
    stopSpeaking();
    setPlaying(false);
    setNote('');
  }, [bcp47, text]);

  if (!canSpeak() || !text) return null;

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (playing) {
      stopSpeaking();
      setPlaying(false);
      return;
    }
    if (!findVoice(bcp47) && window.speechSynthesis.getVoices().length > 0) {
      setNote(t('no_voice'));
    }
    speak(text, bcp47, () => setPlaying(false));
    setPlaying(true);
  };

  return (
    <span className="listen-wrap">
      <button type="button" className={className} onClick={toggle} aria-pressed={playing}>
        {playing ? <Square size={13} aria-hidden="true" /> : <Volume2 size={13} aria-hidden="true" />}
        <span>{playing ? t('stop') : t('listen')}</span>
      </button>
      {note && <span className="listen-note">{note}</span>}
    </span>
  );
}
