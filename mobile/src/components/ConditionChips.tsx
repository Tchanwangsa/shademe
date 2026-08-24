import { View, Text } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import type { Conditions } from '../lib/api';
import { conditionIcon, uvColor } from '../lib/format';
import { useTheme } from '../lib/theme';
import { Card } from './ui/Card';

/** Two chips floating over the map: sky + temperature, and the UV index.
 *
 * Glyph and number, nothing else. "Partly cloudy" under a temperature and "High" under
 * a UV index are both restatements -- the icon already says what the sky is doing and
 * the UV colour already says how severe the index is (see format.uvColor). A chip is
 * read in a glance while walking, so anything it says twice it says at the cost of the
 * thing it says once.
 *
 * No clock either. The engine prices the wall clock and nothing else, so a time readout
 * would only invite the question of whether it can be changed -- it cannot. That is now
 * true of every hour: the 06..20 clamp is gone, so the glyph is the sky at THIS hour and
 * `night` is one of the states it can be.
 */
export function ConditionChips({ conditions }: { conditions: Conditions | null }) {
  const theme = useTheme();
  if (!conditions) return null;

  return (
    <View className="flex-row flex-wrap items-start gap-2">
      <Card className="flex-row items-center gap-2 px-3 py-2">
        {/* Only the sun is drawn in the sun colour. A gold moon would read as the same
            state at a glance, which is the confusion this whole chip exists to avoid. */}
        <Ionicons
          name={conditionIcon[conditions.condition] as any}
          size={20}
          color={conditions.condition === 'sunny' ? theme.sun : theme.inkMuted}
        />
        <Text className="text-lg font-semibold leading-6 text-ink dark:text-paper">
          {Math.round(conditions.temperature)}°
        </Text>
      </Card>

      {/* Hidden outright when the index is unknown. There is no estimate to fall back
          on -- see server/uv.py -- so an empty slot is the honest render. */}
      {conditions.uv_index != null ? (
        <Card className="flex-row items-baseline gap-1.5 px-3 py-2">
          <Text className="text-[13px] font-medium leading-6 text-ink-soft">UV</Text>
          <Text
            className="text-lg font-semibold leading-6"
            style={{ color: uvColor(conditions.uv_index, theme.isDark) }}
          >
            {Math.round(conditions.uv_index)}
          </Text>
        </Card>
      ) : null}

      {/* Only ever shown when the engine is NOT pricing today. The reading would
          otherwise read as live while being a pinned archive day. */}
      {!conditions.is_today ? (
        <Card className="justify-center px-3 py-2">
          <Text className="text-[11px] leading-tight text-ink-soft">Not today</Text>
          <Text className="text-[13px] font-medium leading-4 text-ink dark:text-paper">
            {conditions.date}
          </Text>
        </Card>
      ) : null}
    </View>
  );
}
