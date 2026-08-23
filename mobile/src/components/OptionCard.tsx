import { Pressable, View, Text } from 'react-native';
import type { RouteOption } from '../lib/api';
import { cn } from '../lib/cn';
import { distance, dose, minutes, pct, seconds } from '../lib/format';
import { Badge } from './ui/Badge';

/** One walking option. The headline badge is the DOSE avoided in degC-minutes, not a
 * percentage: the two do not peak at the same hour and the percentage reads worst at
 * the hottest hour, when the walk is at its most unpleasant.
 */
export function OptionCard({
  option,
  selected,
  onPress,
}: {
  option: RouteOption;
  selected: boolean;
  onPress: () => void;
}) {
  const s = option.summary;
  const avoided = option.avoided.stress_load_avoided;
  const showsBenefit = !option.is_shortest && avoided > 0.5;

  return (
    <Pressable
      onPress={onPress}
      className={cn(
        'rounded-2xl border px-3.5 py-3',
        selected
          ? 'border-2 border-shade bg-shade-bg/40 dark:border-shade-dark dark:bg-shade-fg/15'
          : 'border-line bg-paper dark:border-line-dark dark:bg-night-raised',
      )}
    >
      <View className="flex-row items-baseline justify-between">
        <Text className="text-[17px] font-semibold text-ink dark:text-paper">
          {minutes(s.minutes)}
        </Text>
        {option.label === 'Coolest' ? (
          <Badge tone="shade">Coolest</Badge>
        ) : (
          <Text className="text-[11px] text-ink-soft">{option.label}</Text>
        )}
      </View>

      <Text className="mt-0.5 text-[13px] text-ink-muted dark:text-ink-soft">
        {distance(s.distance_m)}
        {s.indoor_pct >= 1 ? ` · ${pct(s.indoor_pct)} indoors` : ''}
      </Text>

      <View className="mt-2 flex-row flex-wrap gap-1.5">
        {showsBenefit ? <Badge tone="shade">{`${dose(avoided)} less heat`}</Badge> : null}
        <Badge tone={s.sun_pct > 60 ? 'sun' : 'neutral'}>
          {`${distance(s.sun_m)} in sun`}
        </Badge>
        {showsBenefit && option.avoided.extra_s >= 1 ? (
          <Badge>{seconds(option.avoided.extra_s)}</Badge>
        ) : null}
      </View>
    </Pressable>
  );
}
