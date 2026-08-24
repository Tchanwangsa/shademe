import { useState } from 'react';
import { Pressable, Switch, Text, View } from 'react-native';
import Ionicons from '@expo/vector-icons/Ionicons';

import { isDefault, type Walker } from '../lib/walker';
import { useTheme } from '../lib/theme';

/** What each declared flag is called once it is folded into the collapsed summary.
 *  Short enough to sit on one line beside the header, and phrased as the QUESTION that
 *  was answered rather than as a category of person. */
const SHORT: Record<keyof Walker, string> = {
  unacclimatised: 'New to this heat',
  vulnerable: 'Less heat tolerance',
};

function Row({
  title,
  detail,
  value,
  onChange,
}: {
  title: string;
  detail: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  const theme = useTheme();
  return (
    // The whole row toggles, not just the switch. A 51 pt switch is a small target for
    // someone who is hot, in the sun, and holding a phone one-handed.
    <Pressable onPress={() => onChange(!value)} className="flex-row items-center gap-3 py-2.5">
      <View className="flex-1">
        <Text className="text-[15px] text-ink dark:text-paper">{title}</Text>
        <Text className="mt-0.5 text-[12px] leading-4 text-ink-soft">{detail}</Text>
      </View>
      <Switch
        value={value}
        onValueChange={onChange}
        trackColor={{ false: theme.line, true: theme.shade }}
      />
    </Pressable>
  );
}

/**
 * The two questions that set this walker's K -- how much further a walk is worth to
 * avoid one degree of heat stress.
 *
 * TWO QUESTIONS, NOT ONE SLIDER, because the two reasons are independent and a person
 * can be either, both or neither. Acclimatisation is recent history and wears off;
 * vulnerability is capacity and does not. Collapsing them into one "how sensitive are
 * you?" dial would ask someone to average two things they can answer exactly.
 *
 * COLLAPSED UNTIL ASKED FOR. Most walks are planned by someone who will answer no twice,
 * and a health panel sitting open above the route options makes them read the app as
 * being about their body rather than about the street. Collapsed, it states what is set
 * in three words and gets out of the way; expanded, it is two switches and a sentence.
 *
 * WHAT THE FOOTER HAS TO SAY, and why it is not optional: this changes a preference, not
 * a diagnosis, and the ceiling is quoted from the server's own `detour_cap` rather than
 * written into the copy. Whatever these switches are set to, no route comes back longer
 * than that multiple of the direct walk -- which is the reason an uncalibrated number is
 * safe to put in front of someone. A cap the user can read is worth more than a
 * disclaimer they cannot check.
 */
export function HeatSensitivity({
  walker,
  onChange,
  detourCap,
}: {
  walker: Walker;
  onChange: (w: Walker) => void;
  /** `meta.detour_cap` from the last /routes response; null before there has been one. */
  detourCap?: number | null;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);

  const set = (Object.keys(SHORT) as (keyof Walker)[]).filter((k) => walker[k]);
  const summary = set.length ? set.map((k) => SHORT[k]).join(' · ') : 'Standard';
  const capPct = detourCap != null ? Math.round((detourCap - 1) * 100) : null;

  return (
    <View className="mt-2 rounded-xl bg-paper-sunken px-3 dark:bg-night">
      <Pressable
        onPress={() => setOpen((o) => !o)}
        className="flex-row items-center gap-2.5 py-2.5"
      >
        <Ionicons
          name="body-outline"
          size={17}
          color={isDefault(walker) ? theme.inkSoft : theme.shade}
        />
        <View className="flex-1">
          <Text className="text-[15px] text-ink dark:text-paper">Heat sensitivity</Text>
          <Text
            className={
              isDefault(walker) ? 'text-[12px] text-ink-soft' : 'text-[12px] text-shade-fg dark:text-shade-dark'
            }
            numberOfLines={1}
          >
            {summary}
          </Text>
        </View>
        <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={16} color={theme.inkSoft} />
      </Pressable>

      {open ? (
        <View className="border-t border-line pb-3 pt-1 dark:border-line-dark">
          <Row
            title="Not used to heat like this"
            detail="Visiting, newly arrived, or it is the season's first hot spell — bodies take 1–2 weeks to adapt"
            value={walker.unacclimatised}
            onChange={(v) => onChange({ ...walker, unacclimatised: v })}
          />
          <Row
            title="65+, pregnant, or a heart or kidney condition"
            detail="Less capacity to shed heat, however long you have lived here"
            value={walker.vulnerable}
            onChange={(v) => onChange({ ...walker, vulnerable: v })}
          />
          <Text className="mt-1.5 text-[12px] leading-4 text-ink-soft">
            {'Either answer makes shade worth more walking to you, and changes which route is recommended. It is a preference, not medical advice'}
            {capPct != null
              ? ` — and whatever you set, no route comes back more than ${capPct}% longer than walking direct.`
              : '.'}
          </Text>
        </View>
      ) : null}
    </View>
  );
}
