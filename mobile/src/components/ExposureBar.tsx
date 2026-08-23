import { View, Text } from 'react-native';
import type { RouteOption } from '../lib/api';
import { exposureSlices, type Exposure } from '../lib/format';
import { useTheme } from '../lib/theme';

const LABEL: Record<Exposure, string> = { sun: 'sun', shade: 'shade', indoor: 'indoors' };

/** Exposure along the walk, start to finish, in proportion to distance.
 *
 * Bands narrower than ~14% of the bar lose their label rather than their width: the
 * shape of the walk is the point, and a 30 m dash of sun between two arcades is real.
 */
export function ExposureBar({ option }: { option: RouteOption }) {
  const theme = useTheme();
  const slices = exposureSlices(option);
  const total = slices.reduce((a, s) => a + s.length, 0) || 1;
  const color: Record<Exposure, string> = {
    sun: theme.sun,
    shade: theme.shade,
    indoor: theme.indoor,
  };
  // The dark palette lightens these bands, so a white label on them fails contrast.
  // The label tracks the band, not the theme's foreground.
  const label = theme.isDark ? '#141613' : '#FFFFFF';

  return (
    <View>
      <View className="h-7 flex-row overflow-hidden rounded-lg">
        {slices.map((s, i) => {
          const share = s.length / total;
          return (
            <View
              key={i}
              className="items-center justify-center"
              style={{ flex: share, backgroundColor: color[s.exposure] }}
            >
              {share > 0.14 ? (
                <Text
                  className="text-[11px] font-medium"
                  style={{ color: label }}
                  numberOfLines={1}
                >
                  {LABEL[s.exposure]}
                </Text>
              ) : null}
            </View>
          );
        })}
      </View>
      <Text className="mt-1.5 text-xs text-ink-soft">start → finish</Text>
    </View>
  );
}
