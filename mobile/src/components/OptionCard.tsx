import { Pressable, View, Text } from 'react-native';
import type { OptionLabel, RouteOption } from '../lib/api';
import { cn } from '../lib/cn';
import { distance, dose, minutes, pct, seconds, uvDose } from '../lib/format';
import { Badge } from './ui/Badge';

/** How each earned label renders. `Least UV` and the comfort badge are the two things
 * the engine actually optimised for, so they get colour; `Shortest` and `Balanced` are
 * statements of fact about the route and stay plain. `Warmest` is the same badge as
 * `Coolest` pointing the other way -- see OptionLabel -- so it renders identically. */
const LABEL_TONE: Record<OptionLabel, 'shade' | 'indoor' | null> = {
  Coolest: 'shade',
  Warmest: 'shade',
  'Least UV': 'indoor',
  Shortest: null,
  Balanced: null,
};

/** One walking option.
 *
 * Benefits are quoted as DOSES -- degC-minutes and UV index-minutes -- never as
 * percentages. The two do not peak at the same hour and the percentage reads worst at
 * the hottest hour, when the walk is at its most unpleasant.
 *
 * THE PICK IS A BADGE, NOT A FILTER. Every option the engine found is still on the list
 * in the same order; one of them carries `recommended`, and the wording changes with
 * whether the person told us anything about themselves. "Best for you" is only allowed
 * to say "for you" when there is a you -- with the heat-sensitivity dial untouched the
 * K behind the pick is the model's default, which belongs to nobody in particular, and
 * the badge says "Recommended" instead.
 */
export function OptionCard({
  option,
  selected,
  personalised = false,
  onPress,
}: {
  option: RouteOption;
  selected: boolean;
  /** True when the walker has answered at least one heat-sensitivity question. */
  personalised?: boolean;
  onPress: () => void;
}) {
  const s = option.summary;
  const heatSaved = option.avoided.stress_load_avoided;
  const uvSaved = option.avoided.uv_dose_avoided ?? 0;
  const showsHeat = !option.is_shortest && heatSaved > 0.5;
  const showsUv = !option.is_shortest && uvSaved > 0.5;

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
      <View className="flex-row items-center justify-between">
        <Text className="text-[17px] font-semibold text-ink dark:text-paper">
          {minutes(s.minutes)}
        </Text>
        <View className="flex-row items-center gap-1.5">
          {option.recommended ? (
            <Badge tone="pick">{personalised ? 'Best for you' : 'Recommended'}</Badge>
          ) : null}
          {option.labels.map((l) =>
            LABEL_TONE[l] ? (
              <Badge key={l} tone={LABEL_TONE[l]!}>
                {l}
              </Badge>
            ) : (
              <Text key={l} className="text-[11px] text-ink-soft">
                {l}
              </Text>
            ),
          )}
        </View>
      </View>

      <Text className="mt-0.5 text-[13px] text-ink-muted dark:text-ink-soft">
        {distance(s.distance_m)}
        {s.indoor_pct >= 1 ? ` · ${pct(s.indoor_pct)} indoors` : ''}
      </Text>

      <View className="mt-2 flex-row flex-wrap gap-1.5">
        {showsHeat ? <Badge tone="shade">{`${dose(heatSaved)} less heat`}</Badge> : null}
        {showsUv ? <Badge tone="indoor">{`${uvDose(uvSaved)} less UV`}</Badge> : null}
        <Badge tone={s.sun_pct > 60 ? 'sun' : 'neutral'}>
          {`${distance(s.sun_m)} in sun`}
        </Badge>
        {(showsHeat || showsUv) && option.avoided.extra_s >= 1 ? (
          <Badge>{seconds(option.avoided.extra_s)}</Badge>
        ) : null}
      </View>
    </Pressable>
  );
}
