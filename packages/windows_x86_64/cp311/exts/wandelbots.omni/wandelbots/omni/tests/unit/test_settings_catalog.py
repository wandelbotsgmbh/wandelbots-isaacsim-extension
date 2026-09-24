"""Which carb settings the Preferences window lists, and how each is edited.

The rules live apart from the window so they can be checked against a plain
dict: carb hands back nested dicts for branches and bare values for leaves, and
picking the wrong editor writes the wrong type back into carb.
"""

import omni.kit.test

from wandelbots.omni.ui.preferences.settings_registry import constraint_for
from wandelbots.omni.ui.preferences import (
    BOOL,
    COLOR,
    FOREIGN_GROUP,
    FOREIGN_SETTINGS,
    NUMBER,
    READ_ONLY,
    STRING,
    TEXT,
    classify_setting,
    collect_foreign_settings,
    collect_settings,
    duplicate_keys,
    flatten_settings,
    is_hex_color,
    group_by_extension,
    group_title,
    is_secret,
    readable_words,
    split_extension_path,
)


class TestFlattenSettings(omni.kit.test.AsyncTestCase):
    async def test_a_nested_branch_becomes_leaf_paths(self):
        tree = {"fabric": {"enabled": True}, "port": 8211}

        self.assertEqual(
            [
                ("/exts/wandelbots.omni/fabric/enabled", True),
                ("/exts/wandelbots.omni/port", 8211),
            ],
            sorted(flatten_settings(tree, "/exts/wandelbots.omni")),
        )

    async def test_a_list_stays_one_leaf(self):
        """carb uses a list for an array; its indices are not setting names."""
        tree = {"folders": ["a", "b"]}

        self.assertEqual(
            [("/exts/wandelbots.omni/folders", ["a", "b"])],
            flatten_settings(tree, "/exts/wandelbots.omni"),
        )

    async def test_a_bare_value_is_its_own_leaf(self):
        self.assertEqual([("/x", 1)], flatten_settings(1, "/x"))


class TestClassifySetting(omni.kit.test.AsyncTestCase):
    async def test_a_bool_is_a_toggle(self):
        self.assertEqual(BOOL, classify_setting("/x/enabled", True))

    async def test_a_bool_is_not_mistaken_for_a_number(self):
        """bool is an int in Python, so the order of the checks decides this."""
        self.assertNotEqual(NUMBER, classify_setting("/x/enabled", False))

    async def test_numbers_get_a_number_field(self):
        self.assertEqual(NUMBER, classify_setting("/x/port", 8211))
        self.assertEqual(NUMBER, classify_setting("/x/scale", 1.5))

    async def test_a_plain_string_gets_one_line(self):
        self.assertEqual(STRING, classify_setting("/x/host", "127.0.0.1"))

    async def test_a_string_with_a_newline_gets_the_multi_line_field(self):
        self.assertEqual(TEXT, classify_setting("/x/note", "one\ntwo"))

    async def test_a_named_colour_gets_the_picker(self):
        self.assertEqual(COLOR, classify_setting("/x/preview_color", [0.2, 0.4, 9.0]))

    async def test_a_normalized_triplet_gets_the_picker(self):
        self.assertEqual(COLOR, classify_setting("/x/tint", [0.2, 0.4, 0.6]))

    async def test_an_unnamed_out_of_range_triplet_is_not_a_colour(self):
        """A three-number array can be a position or a scale."""
        self.assertEqual(READ_ONLY, classify_setting("/x/offset", [10.0, 2.0, 3.0]))

    async def test_a_hex_colour_string_gets_the_picker(self):
        """Colours are stored as hex here, not as float arrays."""
        self.assertEqual(COLOR, classify_setting("/x/overlay_color", "#A936DA16"))

    async def test_a_six_digit_hex_colour_also_counts(self):
        self.assertEqual(COLOR, classify_setting("/x/overlay_color", "#A936DA"))

    async def test_a_colour_named_key_holding_prose_stays_a_string(self):
        self.assertEqual(STRING, classify_setting("/x/overlay_color", "dark red"))

    async def test_a_hex_string_under_another_name_stays_a_string(self):
        self.assertEqual(STRING, classify_setting("/x/checksum", "A936DA16"))

    async def test_a_list_of_strings_is_shown_only(self):
        self.assertEqual(READ_ONLY, classify_setting("/x/folders", ["a", "b"]))


class TestIsSecret(omni.kit.test.AsyncTestCase):
    async def test_a_token_store_is_a_secret(self):
        self.assertTrue(
            is_secret("/persistent/exts/wandelbots.omni/nucleus/api_tokens")
        )

    async def test_an_ordinary_setting_is_not(self):
        self.assertFalse(is_secret("/exts/wandelbots.omni/fabric/enabled"))


class TestCollectSettings(omni.kit.test.AsyncTestCase):
    def _tree_of(self, tree: dict):
        return lambda path: tree.get(path)

    async def test_only_the_extensions_own_nodes_are_listed(self):
        tree_of = self._tree_of(
            {
                "/exts": {
                    "wandelbots.omni": {"fabric": {"enabled": True}},
                    "omni.kit.window.script_editor": {"snippetFolders": ["x"]},
                    "wandelbotsX": {"nope": 1},
                }
            }
        )

        self.assertEqual(
            ["/exts/wandelbots.omni/fabric/enabled"],
            [setting.path for setting in collect_settings(tree_of)],
        )

    async def test_persistent_and_volatile_roots_are_both_scanned(self):
        tree_of = self._tree_of(
            {
                "/exts": {"wandelbots.omni": {"a": 1}},
                "/persistent/exts": {"wandelbots.omni": {"b": 2}},
            }
        )

        self.assertEqual(
            ["/exts/wandelbots.omni/a", "/persistent/exts/wandelbots.omni/b"],
            [setting.path for setting in collect_settings(tree_of)],
        )

    async def test_a_path_reachable_from_two_roots_is_listed_once(self):
        """ "/" also contains "exts", so the same leaf is walked twice."""
        tree_of = self._tree_of(
            {
                "/exts": {"wandelbots.omni": {"a": 1}},
                "/": {"wandelbots.omni": {"a": 1}},
            }
        )

        paths = [setting.path for setting in collect_settings(tree_of)]
        self.assertEqual(len(paths), len(set(paths)))

    async def test_a_root_carb_does_not_have_is_skipped(self):
        self.assertEqual([], collect_settings(lambda _path: None))

    async def test_the_key_is_the_path_below_the_extension(self):
        """Not the last segment: several extensions carry more than one
        overlay_color, and three rows reading "overlay_color" say nothing."""
        tree_of = self._tree_of(
            {"/exts": {"wandelbots.omni": {"fabric": {"enabled": True}}}}
        )

        self.assertEqual("fabric/enabled", collect_settings(tree_of)[0].key)

    async def test_the_label_reads_the_key_back_in_words(self):
        tree_of = self._tree_of(
            {"/exts": {"wandelbots.omni": {"fabric": {"enabled": True}}}}
        )

        self.assertEqual("Fabric Enabled", collect_settings(tree_of)[0].label)

    async def test_a_secret_is_left_out_entirely(self):
        tree_of = self._tree_of(
            {
                "/persistent/exts": {
                    "wandelbots.omni": {
                        "nucleus": {"api_tokens": "{}"},
                        "fabric": {"enabled": True},
                    }
                }
            }
        )

        self.assertEqual(
            ["/persistent/exts/wandelbots.omni/fabric/enabled"],
            [setting.path for setting in collect_settings(tree_of)],
        )


class TestCollectForeignSettings(omni.kit.test.AsyncTestCase):
    _FIXED_TIME_STEPPING = "/app/player/useFixedTimeStepping"

    async def test_a_declared_setting_carb_holds_is_listed(self):
        values = {self._FIXED_TIME_STEPPING: True}

        settings = collect_foreign_settings(values.get)

        self.assertEqual([self._FIXED_TIME_STEPPING], [s.path for s in settings])
        self.assertEqual(BOOL, settings[0].kind)

    async def test_a_declared_setting_carb_lacks_is_skipped(self):
        self.assertEqual([], collect_foreign_settings({}.get))

    async def test_it_is_grouped_apart_from_the_extensions(self):
        values = {self._FIXED_TIME_STEPPING: False}

        (setting,) = collect_foreign_settings(values.get)

        self.assertEqual(FOREIGN_GROUP, setting.extension_name)
        self.assertEqual({FOREIGN_GROUP: [setting]}, group_by_extension([setting]))

    async def test_the_declared_label_replaces_the_key_words(self):
        values = {self._FIXED_TIME_STEPPING: False}

        (setting,) = collect_foreign_settings(values.get)

        self.assertEqual("Use Fixed Time Stepping", setting.label)

    async def test_the_tooltip_explains_and_still_names_the_path(self):
        values = {self._FIXED_TIME_STEPPING: False}

        (setting,) = collect_foreign_settings(values.get)

        self.assertTrue(setting.tooltip.startswith(setting.description))
        self.assertTrue(setting.tooltip.endswith(self._FIXED_TIME_STEPPING))

    async def test_undeclared_paths_are_not_read(self):
        read_paths = []

        def value_of(path):
            read_paths.append(path)

        collect_foreign_settings(value_of)

        self.assertEqual([declared.path for declared in FOREIGN_SETTINGS], read_paths)

    async def test_an_own_setting_keeps_the_path_as_its_tooltip(self):
        tree = {"/exts": {"wandelbots.omni": {"a": 1}}}

        (setting,) = collect_settings(tree.get)

        self.assertEqual("/exts/wandelbots.omni/a", setting.tooltip)


class TestSplitExtensionPath(omni.kit.test.AsyncTestCase):
    async def test_a_volatile_path_splits_into_extension_and_rest(self):
        self.assertEqual(
            ("wandelbots.omni", "fabric/enabled"),
            split_extension_path("/exts/wandelbots.omni/fabric/enabled"),
        )

    async def test_a_persistent_path_gives_the_same_extension(self):
        """Both trees belong to one extension, so both land in one group."""
        self.assertEqual(
            "wandelbots.omni",
            split_extension_path("/persistent/exts/wandelbots.omni/a/b")[0],
        )

    async def test_a_path_without_exts_keeps_its_whole_self(self):
        self.assertEqual(
            ("", "persistent/wandelbots/credentials"),
            split_extension_path("/persistent/wandelbots/credentials"),
        )


class TestGroupTitle(omni.kit.test.AsyncTestCase):
    async def test_a_known_extension_shows_both_names(self):
        self.assertEqual(
            "Wandelbots NOVA (wandelbots.omni)",
            group_title("wandelbots.omni", "Wandelbots NOVA"),
        )

    async def test_without_a_title_the_name_stands_alone(self):
        self.assertEqual("wandelbots.omni", group_title("wandelbots.omni", None))

    async def test_a_setting_outside_any_extension_is_grouped_as_other(self):
        self.assertEqual("Other", group_title("", None))


class TestGroupByExtension(omni.kit.test.AsyncTestCase):
    def _tree_of(self, tree: dict):
        return lambda path: tree.get(path)

    async def test_both_trees_of_one_extension_share_a_group(self):
        tree_of = self._tree_of(
            {
                "/exts": {"wandelbots.omni": {"a": 1}},
                "/persistent/exts": {"wandelbots.omni": {"b": 2}},
            }
        )

        groups = group_by_extension(collect_settings(tree_of))

        self.assertEqual(["wandelbots.omni"], list(groups))
        self.assertEqual(
            ["a", "b"], [setting.key for setting in groups["wandelbots.omni"]]
        )


class TestIsHexColor(omni.kit.test.AsyncTestCase):
    async def test_with_and_without_the_hash(self):
        self.assertTrue(is_hex_color("#A936DA16"))
        self.assertTrue(is_hex_color("A936DA"))

    async def test_a_wrong_length_is_not_a_colour(self):
        self.assertFalse(is_hex_color("#A93"))

    async def test_a_non_hex_character_is_not_a_colour(self):
        self.assertFalse(is_hex_color("#A936DZ"))

    async def test_an_empty_string_is_not_a_colour(self):
        self.assertFalse(is_hex_color(""))


class TestReadableWords(omni.kit.test.AsyncTestCase):
    """Turning a carb key into something a reader can act on."""

    async def test_snake_case_becomes_words(self):
        self.assertEqual(
            "Ghost Teaching Overlay Color",
            readable_words("ghost_teaching/overlay_color"),
        )

    async def test_camel_case_becomes_words(self):
        self.assertEqual(
            "External Joint Stream Physics Feedback",
            readable_words("externalJointStream/physicsFeedback"),
        )

    async def test_the_branch_keeps_same_named_settings_apart(self):
        """One extension carries several overlay_color."""
        self.assertNotEqual(
            readable_words("ghost_teaching/overlay_color"),
            readable_words("collision_world/overlay_color"),
        )

    async def test_acronyms_stay_upper_case(self):
        self.assertEqual(
            "MDL To USD Preview Debug Dump",
            readable_words("mdl_to_usd_preview/debug_dump"),
        )

    async def test_a_single_segment_is_just_the_word(self):
        self.assertEqual("Folders", readable_words("folders"))

    async def test_an_empty_key_gives_an_empty_label(self):
        self.assertEqual("", readable_words(""))

    async def test_digits_stay_their_own_word(self):
        self.assertEqual("Link 0 Offset", readable_words("link_0_offset"))

    async def test_an_all_caps_run_is_not_torn_apart(self):
        self.assertEqual("USD Preview", readable_words("USDPreview"))


class TestDuplicateKeys(omni.kit.test.AsyncTestCase):
    """The same key in both trees of one extension.

    Whichever tree a setting lives in is the owning code's choice, so this
    should not happen - but nothing enforces it, and two rows with the same
    label writing to different keys is worth reporting.
    """

    def _tree_of(self, tree: dict):
        return lambda path: tree.get(path)

    async def test_a_key_in_both_trees_is_reported(self):
        tree_of = self._tree_of(
            {
                "/exts": {"wandelbots.omni": {"a": 1}},
                "/persistent/exts": {"wandelbots.omni": {"a": 2}},
            }
        )

        self.assertEqual(["a"], duplicate_keys(collect_settings(tree_of)))

    async def test_distinct_keys_are_not_reported(self):
        tree_of = self._tree_of(
            {
                "/exts": {"wandelbots.omni": {"a": 1}},
                "/persistent/exts": {"wandelbots.omni": {"b": 2}},
            }
        )

        self.assertEqual([], duplicate_keys(collect_settings(tree_of)))

    async def test_the_same_key_under_two_extensions_is_not_a_duplicate(self):
        tree_of = self._tree_of(
            {"/exts": {"wandelbots.omni": {"a": 1}, "wandelbots.other": {"a": 2}}}
        )

        self.assertEqual([], duplicate_keys(collect_settings(tree_of)))


class TestSettingConstraints(omni.kit.test.AsyncTestCase):
    """Settings whose type alone would let the page write an invalid value."""

    async def test_the_motion_command_is_a_fixed_set(self):
        constraint = constraint_for(
            "/persistent/exts/wandelbots.omni/ghost_teaching/motion_command"
        )

        self.assertEqual(("joint_p2p", "cartesian_p2p", "line"), constraint.choices)

    async def test_the_joint_config_count_is_ranged(self):
        """The overlay slices its joint list with this value."""
        constraint = constraint_for(
            "/persistent/exts/wandelbots.omni/ghost_teaching/max_joint_configs"
        )

        self.assertEqual((1, 18), (constraint.minimum, constraint.maximum))

    async def test_an_undeclared_setting_has_no_constraint(self):
        self.assertIsNone(constraint_for("/exts/wandelbots.omni/fabric/enabled"))
