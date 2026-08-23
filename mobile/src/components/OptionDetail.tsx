import { View, Text } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import type { RouteOption, RoutesMeta } from '../lib/api';
import { arrival, degrees, distance, dose, minutes, pct } from '../lib/format';
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

export function OptionDetail({
  option,
  meta,
}: {
  option: RouteOption;
  meta: RoutesMeta;
}) {
  const s = option.summary;
  const feels = s.utci_mean_outdoor;
  const closed = meta.availability.closed_classes;

  return (
    <View>
      <Text className="text-[13px] text-ink-muted dark:text-ink-soft">
        Arrive {arrival(meta.as_of, s.minutes)} · {distance(s.distance_m)}
      </Text>

      <View className="mt-3 flex-row gap-2">
        <Tile
          label="Feels like"
          value={feels != null ? degrees(feels) : '—'}
        />
        <Tile label="In sun" value={minutes(s.sun_minutes)} />
        <Tile label="Climb" value={`${Math.round(s.climb_m)} m`} />
      </View>

      <View className="mt-3">
        <ExposureBar option={option} />
      </View>

      <View className="mt-3 border-t border-line pt-2.5 dark:border-line-dark">
        {!option.is_shortest && option.avoided.stress_load_avoided > 0.5 ? (
          <Note icon="thermometer-outline">
            {`${dose(option.avoided.stress_load_avoided)} less heat than walking direct, for ${Math.round(option.avoided.extra_m)} m further`}
          </Note>
        ) : (
          <Note icon="navigate-outline">
            This is the direct route — there is no cooler way to go right now
          </Note>
        )}

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

        {s.indoor_pct >= 1 ? (
          <Text className="mt-2.5 text-[11px] leading-4 text-ink-soft">
            Indoor legs are priced at a fixed 22.5° — an assumption, not a measurement.
            Most of the difference above comes from air conditioning rather than shade.
          </Text>
        ) : null}

        {meta.provenance ? (
          <Text className="mt-2 text-[10px] leading-4 text-ink-soft" numberOfLines={2}>
            {meta.provenance}
          </Text>
        ) : null}
      </View>
    </View>
  );
}
