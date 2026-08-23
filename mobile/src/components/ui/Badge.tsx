import { View } from 'react-native';
import { Text } from 'react-native';
import { cn } from '../../lib/cn';

type Tone = 'neutral' | 'sun' | 'shade' | 'indoor';

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
