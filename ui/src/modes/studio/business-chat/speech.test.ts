import { describe, expect, it, vi } from 'vitest';

import {
  collectRecognitionText,
  recognitionConstructor,
  speakText,
  speechRecognitionSupported,
  speechSynthesisSupported,
  stopSpeaking,
} from './speech';

describe('browser speech capabilities', () => {
  it('detects the standard and prefixed speech-recognition constructors', () => {
    class Recognition {}
    const standard = { SpeechRecognition: Recognition } as unknown as Window;
    const prefixed = { webkitSpeechRecognition: Recognition } as unknown as Window;
    expect(recognitionConstructor(standard)).toBe(Recognition);
    expect(recognitionConstructor(prefixed)).toBe(Recognition);
    expect(speechRecognitionSupported({} as Window)).toBe(false);
  });

  it('collects final and interim transcript fragments safely', () => {
    expect(collectRecognitionText({
      resultIndex: 0,
      results: [
        { isFinal: true, 0: { transcript: 'Tell me about' } },
        { isFinal: false, 0: { transcript: ' a challenge' } },
      ],
    })).toEqual({ transcript: 'Tell me about a challenge', final: false });
  });

  it('speaks and stops only when synthesis is available', () => {
    const speak = vi.fn();
    const cancel = vi.fn();
    class Utterance {
      rate = 0;
      pitch = 0;
      text: string;
      constructor(text: string) { this.text = text; }
    }
    const scope = {
      speechSynthesis: { speak, cancel },
      SpeechSynthesisUtterance: Utterance,
    } as unknown as Window;
    expect(speechSynthesisSupported(scope)).toBe(true);
    expect(speakText('Practice answer', scope)).toBe(true);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(speak.mock.calls[0][0].text).toBe('Practice answer');
    stopSpeaking(scope);
    expect(cancel).toHaveBeenCalledTimes(2);
  });
});