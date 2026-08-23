import { View, Text } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';
import type { Conditions } from '../lib/api';
import { conditionIcon, conditionLabel, uvBand } from '../lib/format';
import { useTheme } from '../lib/theme';
import { Card } from './ui/Card';

/** Two separate chips floating over the map: sky + temperature, and UV.
 *
 * No clock. The engine prices the wall clock and nothing else, so a time readout here
 * would only invite the question of whether it can be changed -- it cannot.
 */
export function ConditionChips({ conditions }: { conditions: Conditions | null }) {
  const theme = useTheme();
  if (!conditions) return null;
  const uv = uvBand(conditions.uv_index);
  const uvColor = uv.tone === 'low' ? theme.shade : theme.sun;

  return (
    <View className="flex-row gap-2">
      <Card className="flex-row items-center gap-2.5 px-3 py-2">
        <Ionicons
          name={conditionIcon[conditions.condition] as any}
          size={20}
          color={conditions.condition === 'sunny' ? theme.sun : theme.inkMuted}
        />
        <View>
          <Text className="text-lg font-semibold leading-5 text-ink dark:text-paper">
            {Math.round(conditions.temperature)}°
          </Text>
          <Text className="text-[11px] leading-tight text-ink-soft">
            {conditionLabel[conditions.condition]}
          </Text>
        </View>
      </Card>

      <Card className="justify-center px-3 py-2">
        <Text className="text-lg font-semibold leading-5 text-ink dark:text-paper">
          UV {conditions.uv_index.toFixed(0)}
        </Text>
        <Text className="text-[11px] leading-tight" style={{ color: uvColor }}>
          {uv.label}
        </Text>
      </Card>
    </View>
  );
}
