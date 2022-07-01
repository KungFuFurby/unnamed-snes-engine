# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import os.path

from collections import OrderedDict

from _common import MemoryMapMode

from typing import Any, Callable, Final, Generator, NamedTuple, NoReturn, Optional, Type, TypeVar, Union



Name       = str
ScopedName = str
RoomName   = str

Filename   = str



class JsonError(Exception):
    def __init__(self, message : str, path : tuple[str, ...]):
        self.message : Final = message
        self.path    : Final = path

    def __str__(self) -> str:
        return f"{ self.location_string() }: { self.message }"

    def location_string(self) -> str:
        return ' '.join(self.path)



class _Helper:
    """
    A helper class to help parse the output of `json.load()` into structured data.

    This class will also recursively track the position within the `json.load()` output to improve error messages.
    """

    # _Helper class or subclass of _Helper
    _Self = TypeVar('_Self', bound='_Helper')

    _T = TypeVar('_T')
    _U = TypeVar('_U')


    NAME_REGEX          : Final = re.compile(r'[a-zA-Z0-9_]+$')
    NAME_WITH_DOT_REGEX : Final = re.compile(r'[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$')
    ROOM_NAME_REGEX     : Final = re.compile(r'[a-zA-Z0-9_-]+$')


    def __init__(self, d : dict[str, Any], *path : str):
        if not isinstance(d, dict):
            raise JsonError('Expected a dict', path)

        self.__dict : Final = d
        self.__path : Final = path


    def _raise_error(self, e : Union[str, Exception], *location : str) -> NoReturn:
        if isinstance(e, Exception):
            e = f"{ type(e).__name__ }: { e }"
        raise JsonError(e, self.__path + location) from None


    def _raise_missing_field_error(self, key : str, *location : str) -> NoReturn:
        raise JsonError(f"Missing JSON field: { key }", self.__path + location)


    def contains(self, key : str) -> bool:
        return key in self.__dict


    def _get(self, key : str, _type : Type[_T]) -> _T:
        assert _type != dict and _type != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            self._raise_missing_field_error(key)
        if not isinstance(v, _type):
            self._raise_error(f"Expected a { _type.__name__ }", key)
        return v


    def _get2(self, key : str, type_a : Type[_T], type_b : Type[_U]) -> Union[_T, _U]:
        assert type_a != dict and type_a != OrderedDict
        assert type_b != dict and type_b != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            self._raise_missing_field_error(key)
        if not isinstance(v, type_a) and not isinstance(v, type_b):
            self._raise_error(f"Expected a { type_a.__name__ } or { type_b.__name__ }", key)
        return v


    def _optional_get(self, key : str, _type : Type[_T]) -> Optional[_T]:
        assert _type != dict and _type != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            return None
        if not isinstance(v, _type):
            self._raise_error(f"Expected a { _type.__name__ }", key)
        return v


    def _optional_get2(self, key : str, type_a : Type[_T], type_b : Type[_U]) -> Optional[Union[_T, _U]]:
        assert type_a != dict and type_a != OrderedDict
        assert type_b != dict and type_b != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            return None
        if not isinstance(v, type_a) and not isinstance(v, type_b):
            self._raise_error(f"Expected a { type_a.__name__ } or { type_b.__name__ }", key)
        return v


    def get_optional_dict(self : _Self, key : str) -> Optional[_Self]:
        cls = type(self)

        d = self.__dict.get(key)
        if d is None:
            return None
        if not isinstance(d, dict):
            self._raise_error(f"Expected a JSON dict type", key)

        return cls(d, *self.__path, key)


    def get_dict(self : _Self, key : str) -> _Self:
        cls = type(self)

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error(f"Expected a JSON dict type", key)

        return cls(d, *self.__path, key)


    def iterate_list_of_dicts(self : _Self, key : str) -> Generator[_Self, None, None]:
        cls = type(self)

        for i, item in enumerate(self._get(key, list)):
            if not isinstance(item, dict):
                self._raise_error(f"Expected a dict", key)

            yield cls(item, *self.__path, key, str(i))


    def iterate_dict_of_dicts(self : _Self, key : str) -> Generator[tuple[Name, _Self], None, None]:
        cls = type(self)

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error(f"Expected a JSON dict type", key)

        for name, item in d.items():
            name = self._test_name(name, key)

            if not isinstance(item, dict):
                self._raise_error('Expected a dict', key, name)

            yield name, cls(item, *self.__path, key)


    def iterate_dict(self, key : str, _type : Type[_T]) -> Generator[tuple[Name, _T], None, None]:
        assert _type != dict or _type != OrderedDict

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error(f"Expected a JSON dict type", key)

        for name, item in d.items():
            name = self._test_name(name, key)

            if not isinstance(item, _type):
                self._raise_error(f"Expected a { _type.__name__ }", key, name)

            yield name, item


    # `self.__dict` MUST NOT be accessed below this line
    # --------------------------------------------------


    def get_string(self, key : str) -> str:
        return self._get(key, str)


    def get_optional_string(self, key : str) -> Optional[str]:
        return self._optional_get(key, str)


    def get_filename(self, key : str) -> Filename:
        return self._get(key, str)


    def get_int(self, key : str) -> int:
        v = self._get2(key, str, int)
        if isinstance(v, int):
            return v
        else:
            try:
                return int(v)
            except ValueError:
                self._raise_error('Expected an integer', key)


    def get_float(self, key : str) -> float:
        return self._get2(key, int, float)


    def get_hex_or_int(self, key : str) -> int:
        v = self._get2(key, int, str)
        if isinstance(v, int):
            return v
        else:
            try:
                return int(v, 16)
            except ValueError:
                self._raise_error(f"Expected hex string: { v }", key)


    def get_bool(self, key : str) -> bool:
        v = self._optional_get(key, bool)

        if v is None:
            return False
        return v


    def get_object_size(self, key : str) -> int:
        i = self.get_int(key)
        if i not in (8, 16):
            self._raise_error(f"Invalid Object Size: { i }", key)
        return i


    def get_name(self, key : str) -> Name:
        s = self.get_string(key)
        if self.NAME_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid name: {s}", key)


    def get_name_with_dot(self, key : str) -> ScopedName:
        s = self.get_string(key)
        if self.NAME_WITH_DOT_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid name with dot: {s}", key)


    def get_optional_name(self, key : str) -> Optional[Name]:
        s = self.get_optional_string(key)
        if not s:
            return None
        if self.NAME_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid name with dot: {s}", key)


    def get_room_name(self, key : str) -> RoomName:
        s = self.get_string(key)
        if self.ROOM_NAME_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid room name: {s}", key)


    def get_name_list(self, key : str) -> list[Name]:
        l = self._get(key, list)

        for i, s in enumerate(l):
            if not isinstance(s, str):
                self._raise_error('Expected a string', key, str(i))
            if not self.NAME_REGEX.match(s):
                self._raise_error(f"Invalid name: {s}", key, str(i))
        return l


    def get_name_list_mapping(self, key : str, max_items : Optional[int] = None) -> OrderedDict[Name, int]:
        l = self.get_name_list(key)

        out : OrderedDict[Name, int] = OrderedDict()

        for i, s in enumerate(l):
            if s in out:
                self._raise_error(f"Duplicate name: { s }", key, str(i))
            out[s] = i

        return out


    def _test_name(self, s : Any, *path : str) -> Name:
        if not isinstance(s, str):
            self._raise_error('Expected a string', *path)
        if not self.NAME_REGEX.match(s):
            self._raise_error(f"Invalid name: {s}", *path)
        return s


    def _test_name_list(self, l : Any, *path : str) -> list[Name]:
        if not isinstance(l, list):
            self._raise_error('Expected a list of names', *path)

        for i, s in enumerate(l):
            if not isinstance(s, str):
                self._raise_error('Expected a string', *path, str(i))
            if not self.NAME_REGEX.match(s):
                self._raise_error(f"Invalid name: {s}", *path, str(i))
        return l


    def build_dict_from_dict(self : _Self, key : str, _type : Type[_T], max_elements : int,
                             func : Callable[[_Self, Name], _T]) -> dict[Name, _T]:
        out : dict[Name, _Helper._T] = dict()

        for item_name, d in self.iterate_dict_of_dicts(key):
            # item_name has been checked by `iterate_dict_of_dicts`

            if item_name in out:
                self._raise_error(f"Duplicate { _type.__name__ } name: { item_name }", key)

            try:
                item = func(d, item_name)
            except JsonError:
                raise
            except Exception as ex:
                self._raise_error(ex, key, item_name)

            assert item_name not in out
            out[item_name] = item

        if len(out) > max_elements:
            self._raise_error(f"Too many items ({ len(out) }, max: { max_elements })", key)

        return out


    def build_ordered_dict_from_list(self : _Self, key : str, _type : Type[_T], max_elements : int,
                                     func : Callable[[_Self, Name, int], _T]) -> OrderedDict[Name, _T]:
        cls = type(self)

        out : OrderedDict[Name, _Helper._T] = OrderedDict()

        for i, d in enumerate(self._get(key, list)):
            if not isinstance(d, dict):
                self._raise_error('Expected a JSON dict', key, str(i))

            # Testing 'name' here improves the error messages in the child `cls` instance.
            item_name : Optional[Name] = d.get('name')
            if item_name is None:
                self._raise_missing_field_error('name', key)
            if not isinstance(item_name, str):
                self._raise_error(f"Expected a string", key, str(i), 'name')
            if not self.NAME_REGEX.match(item_name):
                self._raise_error(f"Invalid name: { item_name }", key, str(i), 'name')

            if item_name in out:
                self._raise_error(f"Duplicate { _type.__name__ } name: { item_name }", key, str(i))

            item_index_str = f"{i} ({item_name})"
            try:
                item = func(cls(d, *self.__path, key, item_index_str), item_name, i)
            except JsonError:
                raise
            except Exception as ex:
                self._raise_error(ex, key, item_index_str)

            assert item_name not in out
            out[item_name] = item

        if len(out) > max_elements:
            self._raise_error(f"Too many items ({ len(out) }, max: { max_elements })", key)

        return out



def _load_json_file(filename : Filename, cls : Type[_Helper._Self]) -> _Helper._Self:
    basename = os.path.basename(filename)

    with open(filename, 'r') as fp:
        j = json.load(fp)

    return cls(j, os.path.basename(filename))



# entities.json
# =============


class EfParameter(NamedTuple):
    type    : str
    values  : list[Name]


class EntityFunction(NamedTuple):
    name                        : Name
    id                          : int
    ms_export_order             : Name
    parameter                   : Optional[EfParameter]
    uses_process_function_from  : Optional[Name]


class EntityVision(NamedTuple):
    a   : int
    b   : int


class Entity(NamedTuple):
    name        : Name
    id          : int
    code        : EntityFunction
    metasprites : ScopedName
    zpos        : int
    vision      : Optional[EntityVision]
    health      : int
    attack      : int


class EntitiesJson(NamedTuple):
    entity_functions : OrderedDict[Name, EntityFunction]
    entities         : OrderedDict[Name, Entity]



class _Entities_Helper(_Helper):
    def get_entity_vision(self, key : str) -> Optional[EntityVision]:
        s = self.get_optional_string(key)
        if not s:
            return None
        v = s.split()
        if len(v) != 2:
            self._raise_error('Expected a string containing two integers', key)

        try:
            return EntityVision(int(v[0]), int(v[1]))
        except ValueError:
            self._raise_error('Expected a string containing two integers', key)


    def get_ef_parameter(self, key : str) -> Optional[EfParameter]:
        p = self.get_optional_dict(key)
        if p is None:
            return None

        t = p.get_string('type')

        if t == 'enum':
            return EfParameter('enum', p.get_name_list('values'))
        else:
            self._raise_error(f"Unknown function parameter type: { t }", key)



def load_entities_json(filename : Filename) -> EntitiesJson:
    jh = _load_json_file(filename, _Entities_Helper)

    entity_functions = jh.build_ordered_dict_from_list('entity_functions', EntityFunction, 256,
            lambda ef, name, i : EntityFunction(
                name = name,
                id = i,
                ms_export_order = ef.get_name('ms-export-order'),
                parameter = ef.get_ef_parameter('parameter'),
                uses_process_function_from = ef.get_optional_name('uses-process-function-from'),
        ))

    entities = jh.build_ordered_dict_from_list('entities', Entity, 254,
            lambda e, name, i : Entity(
                name = name,
                id = i,
                code = entity_functions[e.get_name('code')],
                metasprites = e.get_name_with_dot('metasprites'),
                zpos = e.get_int('zpos'),
                vision = e.get_entity_vision('vision'),
                health = e.get_int('health'),
                attack = e.get_int('attack'),
        ))

    return EntitiesJson(entity_functions=entity_functions, entities=entities)




# ms-export-order.json
# ====================


class MsPatternObject(NamedTuple):
    xpos : int
    ypos : int
    size : int


class MsPattern(NamedTuple):
    name    : Name
    id      : int
    objects : list[MsPatternObject]


class MsAnimationExportOrder(NamedTuple):
    name        : Name
    animations  : list[Name]


class MsExportOrder(NamedTuple):
    patterns        : OrderedDict[Name, MsPattern]
    shadow_sizes    : OrderedDict[Name, int]
    animation_lists : OrderedDict[Name, MsAnimationExportOrder]



class _MSEO_Helper(_Helper):
    def get_pattern_objects(self, key : str) -> list[MsPatternObject]:
        objs = list()

        for o in self.iterate_list_of_dicts(key):
            objs.append(
                MsPatternObject(
                    xpos = o.get_int('x'),
                    ypos = o.get_int('y'),
                    size = o.get_object_size('size')
                )
            )

        return objs


    def get_animation_eo_lists(self, key : str) -> OrderedDict[Name, MsAnimationExportOrder]:
        out = OrderedDict()

        for name, al in self.iterate_dict(key, list):
            eo = MsAnimationExportOrder(
                    name = name,
                    animations = self._test_name_list(al, key, name),
            )

            if eo.name in out:
                self._raise_error(f"Duplicate name: { eo.name }", key)
            out[eo.name] = eo

        return out



def load_ms_export_order_json(filename : Filename) -> MsExportOrder:
    jh = _load_json_file(filename, _MSEO_Helper)

    patterns = jh.build_ordered_dict_from_list('patterns', MsPattern, 256,
            lambda p, name, i: MsPattern(
                name = name,
                id = i * 2,
                objects = p.get_pattern_objects('objects')
        ))

    shadow_sizes = jh.get_name_list_mapping('shadow_sizes')

    return MsExportOrder(
            patterns = patterns,
            shadow_sizes = jh.get_name_list_mapping('shadow_sizes'),
            animation_lists = jh.get_animation_eo_lists('animation_lists'),
    )



# mappings.json
# =============


class MemoryMap(NamedTuple):
    mode                : MemoryMapMode
    first_resource_bank : int
    n_resource_banks    : int


class Mappings(NamedTuple):
    game_title                  : str
    starting_room               : RoomName
    mt_tilesets                 : list[Name]
    ms_spritesheets             : list[Name]
    tiles                       : list[Name]
    interactive_tile_functions  : list[Name]
    memory_map                  : MemoryMap


class _Mappings_Helper(_Helper):
    def get_memory_map(self, key : str) -> MemoryMap:
        mm = self.get_dict(key)

        mode_str = mm.get_string('mode')
        try:
            mode = MemoryMapMode[mode_str.upper()]
        except ValueError:
            self._raise_error(f"Unknown memory mapping mode: { mode_str }", key)

        return MemoryMap(
            mode = mode,
            first_resource_bank = mm.get_hex_or_int('first_resource_bank'),
            n_resource_banks = mm.get_int('n_resource_banks'),
        )


def load_mappings_json(filename : Filename) -> Mappings:
    jh = _load_json_file(filename, _Mappings_Helper)

    return Mappings(
            game_title = jh.get_string('game_title'),
            starting_room = jh.get_room_name('starting_room'),
            mt_tilesets = jh.get_name_list('mt_tilesets'),
            ms_spritesheets = jh.get_name_list('ms_spritesheets'),
            tiles = jh.get_name_list('tiles'),
            interactive_tile_functions = jh.get_name_list('interactive_tile_functions'),
            memory_map = jh.get_memory_map('memory_map'),
    )



# metasprites.json
# ================


class Aabb(NamedTuple):
    x       : int
    y       : int
    width   : int
    height  : int


class MsBlock(NamedTuple):
    pattern         : Optional[Name]
    start           : int
    x               : Optional[int]
    y               : Optional[int]
    flip            : Optional[str]
    frames          : list[Name]
    default_hitbox  : Optional[Aabb]
    default_hurtbox : Optional[Aabb]


class TileHitbox(NamedTuple):
    half_width  : int
    half_height : int


class MsAnimation(NamedTuple):
    name            : Name
    loop            : bool
    delay_type      : str
    fixed_delay     : Optional[Union[float, int]]
    frames          : list[Name]
    frame_delays    : Optional[list[Union[float, int]]]


class MsFrameset(NamedTuple):
    name                : Name
    source              : Filename
    frame_width         : int
    frame_height        : int
    x_origin            : int
    y_origin            : int
    shadow_size         : str
    tilehitbox          : TileHitbox
    default_hitbox      : Optional[Aabb]
    default_hurtbox     : Optional[Aabb]
    pattern             : Optional[Name]
    ms_export_order     : Name
    order               : int
    blocks              : list[MsBlock]
    hitbox_overrides    : dict[Name, Aabb]
    hurtbox_overrides   : dict[Name, Aabb]
    animations          : dict[Name, MsAnimation]


class MsSpritesheet(NamedTuple):
    name        : Name
    palette     : Filename
    first_tile  : int
    end_tile    : int
    framesets   : OrderedDict[Name, MsFrameset]



class _Ms_Helper(_Helper):
    def get_tilehitbox(self, key : str) -> TileHitbox:
        s = self.get_string(key)

        v = s.split()
        if len(v) != 2:
            self._raise_error('Expected a string containing two integers (TileHitbox)', key)

        try:
            return TileHitbox(int(v[0]), int(v[1]))
        except ValueError:
            self._raise_error('Expected a string containing two integers (TileHitbox)', key)


    def get_animation_frames__no_fixed_delay(self, key : str) -> tuple[list[Name], list[Union[int, float]]]:
        l = self._get(key, list)

        if len(l) % 2 != 0:
            self._raise_error('Expected a list of `frame, delay, frame, delay, frame, delay, ...`', key)

        # off indexes
        frames = l[0::2]
        frame_delays = l[1::2]

        for index, s in enumerate(frames):
            if not isinstance(s, str):
                self._raise_error('Expected a str', str(index * 2))

        for index, delay in enumerate(frame_delays):
            if not isinstance(delay, float) and not isinstance(delay, int):
                self._raise_error('Expected a float containing the delay time', str(index * 2 + 1))

        return frames, frame_delays


    def __convert_aabb(self, s : str, *path : str) -> Aabb:
        v = s.split()
        if len(v) != 4:
            self._raise_error('Expected a string containing four integers (Aabb)', *path)
        try:
            return Aabb(int(v[0]), int(v[1]), int(v[2]), int(v[3]))
        except ValueError:
            self._raise_error('Expected a string containing four integers (Aabb)', *path)


    def get_aabb(self, key : str) -> Aabb:
        s = self._get(key, str)
        return self.__convert_aabb(s, key)


    def get_optional_aabb(self, key : str) -> Optional[Aabb]:
        s = self._optional_get(key, str)
        if s is None:
            return None
        return self.__convert_aabb(s, key)


    def get_aabb_overrides(self, key : str) -> dict[Name, Aabb]:
        out : dict[Name, Aabb] = dict()

        if self.contains(key):
            for name, i in self.iterate_dict(key, str):
                if name in out:
                    self._raise_error(f"Duplicate name: { name }", key)
                out[name] = self.__convert_aabb(i, key, name)

        return out


    VALID_FLIPS : Final = ('hflip', 'vflip', 'hvflip')

    def get_flip(self, key : str) -> Optional[str]:
        s = self._optional_get(key, str)
        if not s:
            return None

        if s not in self.VALID_FLIPS:
            self._raise_error(f"Unknown flip: { s }", key)
        return s



def __read_ms_animation(a : _Ms_Helper, name : Name) -> MsAnimation:
    if a.contains('fixed-delay'):
        fixed_delay = a.get_float('fixed-delay')
        frames = a.get_name_list('frames')
        frame_delays = None
    else:
        fixed_delay = None
        frames, frame_delays = a.get_animation_frames__no_fixed_delay('frames')

    return MsAnimation(
            name = name,
            loop = a.get_bool('loop'),
            delay_type = a.get_name('delay-type'),
            fixed_delay = fixed_delay,
            frames = frames,
            frame_delays = frame_delays,
    )




def __read_ms_frameset(jh : _Ms_Helper, name : Name, i : int) -> MsFrameset:
    fs_pattern         : Final = jh.get_optional_name('pattern')
    fs_default_hitbox  : Final = jh.get_optional_aabb('defaultHitbox')
    fs_default_hurtbox : Final = jh.get_optional_aabb('defaultHurtbox')


    blocks = list()
    for b in jh.iterate_list_of_dicts('blocks'):
        pattern = b.get_optional_name('pattern')
        if pattern or fs_pattern:
            x = b.get_int('x')
            y = b.get_int('y')
        else:
            if b.contains('x') or b.contains('y'):
                b._raise_error('MS Blocks with no pattern must not have a `x` or `y` field')
            x = None
            y = None

        hitbox = b.get_optional_aabb('defaultHitbox')
        hurtbox = b.get_optional_aabb('defaultHurtbox')

        blocks.append(
            MsBlock(
                pattern = pattern,
                start = b.get_int('start'),
                x = x,
                y = y,
                flip = b.get_flip('flip'),
                frames = b.get_name_list('frames'),
                default_hitbox = hitbox if hitbox else fs_default_hitbox,
                default_hurtbox = hurtbox if hurtbox else fs_default_hurtbox,
            )
        )


    return MsFrameset(
            name = name,
            source              = jh.get_filename('source'),
            frame_width         = jh.get_int('frameWidth'),
            frame_height        = jh.get_int('frameHeight'),
            x_origin            = jh.get_int('xorigin'),
            y_origin            = jh.get_int('yorigin'),
            shadow_size         = jh.get_name('shadowSize'),
            tilehitbox          = jh.get_tilehitbox('tilehitbox'),
            default_hitbox      = fs_default_hitbox,
            default_hurtbox     = fs_default_hurtbox,
            pattern             = fs_pattern,
            ms_export_order     = jh.get_name('ms-export-order'),
            order               = jh.get_int('order'),
            blocks              = blocks,
            hitbox_overrides    = jh.get_aabb_overrides('hitboxes'),
            hurtbox_overrides   = jh.get_aabb_overrides('hurtboxes'),
            animations          = jh.build_dict_from_dict('animations', MsAnimation, 254, __read_ms_animation),
    )


def _load_metasprites(jh : _Ms_Helper) -> MsSpritesheet:
    return MsSpritesheet(
            name = jh.get_name('name'),
            palette = jh.get_filename('palette'),
            first_tile = jh.get_int('firstTile'),
            end_tile = jh.get_int('endTile'),
            framesets = jh.build_ordered_dict_from_list('framesets', MsFrameset, 256, __read_ms_frameset)
    )



def load_metasprites_json(filename : Filename) -> MsSpritesheet:
    jh = _load_json_file(filename, _Ms_Helper)
    return _load_metasprites(jh)



def load_metasprites_string(text : str) -> MsSpritesheet:
    return _load_metasprites(_Ms_Helper(json.loads(text)))



#
# resources.json
#

class TilesInput(NamedTuple):
    name    : Name
    format  : str
    source  : Filename


class ResourcesJson(NamedTuple):
    tiles   : dict[Name, TilesInput]



def load_resources_json(filename : Filename) -> ResourcesJson:
    jh = _load_json_file(filename, _Helper)

    dirname = os.path.dirname(filename)

    tiles = jh.build_dict_from_dict('tiles', TilesInput, 256,
            lambda t, name: TilesInput(
                name = name,
                format = t.get_string('format'),
                source = os.path.join(dirname, t.get_filename('source')),
    ))

    return ResourcesJson(
            tiles = tiles,
    )


