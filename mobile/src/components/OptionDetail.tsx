import { View, Text } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import type { RouteOption, RoutesMeta } from '../lib/api';
import { arrival, degrees, distance, dose, minutes, pct, uvDose } from '../lib/format';
import { useTheme } from '../lib/theme';
import { ExposureBar } from './ExposureBar';
import { Surface } from './ui/Card';

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <Surface className="flex-1 px-2.5 py-2">
      <Text className="text-[11px] text-ink-soft" numberOfLines={1}>
        {label}
      </Text>
      <Text className="text-lg font-semibold text-ink dark:text-paper" numberOfLines={1}>
        {value}
      </Text>
    </Surface>
  );
}

function Note({ icon, children }: { icon: string; children: React.ReactNode }) {
  const theme = useTheme();
  return (
    <View className="mt-1.5 flex-row items-start gap-2">
      <Ionicons name={icon as any} size={15} color={theme.inkSoft} style={{ marginTop: 1 }} />
      <Text className="flex-1 text-[13px] text-ink-muted dark:text-ink-soft">{children}</Text>
    </View>
  );
}

/** The expanded detail for the selected option.
 *
 * Notes here describe THIS WALK and nothing else. Caveats about the engine's assumptions
 * belong in the code and the contract, not stacked under a route card: a person deciding
 * which way to walk cannot act on them, and they crowd out the two or three facts that
 * do change the decision.
 */
export function OptionDetail({ option, meta }: { option: RouteOption; meta: RoutesMeta }) {
  const s = option.summary;
  const feels = s.utci_mean_outdoor;
  const closed = meta.availability.closed_classes;
  const heatSaved = option.avoided.stress_load_avoided;
  const uvSaved = option.avoided.uv_dose_avoided ?? 0;
  const showsUvTile = s.uv_dose != null && meta.uv_index != null && meta.uv_index > 0;

  return (
    <View>
      <Text className="text-[13px] text-ink-muted dark:text-ink-soft">
        Arrive {arrival(meta.as_of, s.minutes)} · {distance(s.distance_m)}
      </Text>

      <View className="mt-3 flex-row gap-2">
        <Tile label="Feels like" value={feels != null ? degrees(feels) : '—'} />
        <Tile label="In sun" value={minutes(s.sun_minutes)} />
        {showsUvTile ? (
          <Tile label="UV dose" value={uvDose(s.uv_dose!)} />
        ) : (
          <Tile label="Climb" value={`${Math.round(s.climb_m)} m`} />
        )}
      </View>

      <View className="mt-3">
        <ExposureBar option={option} />
      </View>

      <View className="mt-3 border-t border-line pt-2.5 dark:border-line-dark">
        {!option.is_shortest && heatSaved > 0.5 ? (
          <Note icon="thermometer-outline">
            {`${dose(heatSaved)} less heat than walking direct, for ${Math.round(option.avoided.extra_m)} m further`}
          </Note>
        ) : null}

        {!option.is_shortest && uvSaved > 0.5 ? (
          <Note icon="sunny-outline">
            {`${uvDose(uvSaved)} less UV than walking direct${heatSaved > 0.5 ? '' : `, for ${Math.round(option.avoided.extra_m)} m further`}`}
          </Note>
        ) : null}

        {s.doors > 0 ? (
          <Note icon="enter-outline">
            {`${s.doors} door${s.doors === 1 ? '' : 's'}${s.indoor_pct >= 1 ? ` · ${pct(s.indoor_pct)} of the walk is indoors` : ''}`}
          </Note>
        ) : null}

        {closed.length > 0 ? (
          <Note icon="lock-closed-outline">
            {`Routed around what is shut right now: ${closed.join(', ')}`}
          </Note>
        ) : null}
      </View>
    </View>
  );
}
