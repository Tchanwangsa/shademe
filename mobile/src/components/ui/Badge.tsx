import { Text, View } from 'react-native';
import { cn } from '../../lib/cn';

/** `pick` is the only FILLED tone, and it is deliberately the shade colour rather than
 *  a fifth hue: the app has exactly three meaningful colours (sun, shade, indoor) and
 *  the recommendation is not a fourth state a metre of walking can be in. Filling the
 *  existing green is a difference in emphasis, which is what "this one" means, instead
 *  of a new claim. */
type Tone = 'neutral' | 'sun' | 'shade' | 'indoor' | 'pick';

const TONES: Record<Tone, { box: string; text: string }> = {
  neutral: {
    box: 'bg-paper-sunken dark:bg-night',
    text: 'text-ink-muted dark:text-ink-soft',
  },
  sun: { box: 'bg-sun-bg dark:bg-sun-fg/30', text: 'text-sun-fg dark:text-sun-dark' },
  shade: {
    box: 'bg-shade-bg dark:bg-shade-fg/30',
    text: 'text-shade-fg dark:text-shade-dark',
  },
  indoor: {
    box: 'bg-indoor-bg dark:bg-indoor-fg/30',
    text: 'text-indoor-fg dark:text-indoor-dark',
  },
  pick: { box: 'bg-shade dark:bg-shade-dark', text: 'text-paper dark:text-night' },
};

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: string;
  tone?: Tone;
  className?: string;
}) {
  const t = TONES[tone];
  return (
    <View className={cn('rounded-full px-2.5 py-1', t.box, className)}>
      <Text className={cn('text-xs font-medium', t.text)}>{children}</Text>
    </View>
  );
}
