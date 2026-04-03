# OpenHasp - настройка панелей и примеры конфигурации

## 1. Общая структура `panel_config`

Конфигурация панели хранится в поле `Config (JSON)` устройства и загружается плагином при перерисовке страниц.

Базовая структура:

```json
{
  "page_linkedProperty": "%PanelKitchen.page%",
  "idle_linkedProperty": "%PanelKitchen.idle%",
  "brightness_linkedProperty": "%PanelKitchen.brightness%",
  "backlight_linkedProperty": "%PanelKitchen.backlight%",
  "value_event": "up",
  "pages": [
    {
      "comment": "Главная",
      "back": 0,
      "next": 1,
      "prev": 0,
      "objects": []
    }
  ],
  "templates": {}
}
```

## 2. Корневые поля конфигурации

| Поле | Тип | Назначение |
| --- | --- | --- |
| `pages` | array | список страниц панели |
| `templates` | object | библиотека шаблонов для переиспользуемых блоков |
| `value_event` | string | событие, по которому значение с объекта отправляется в `osysHome` |
| `page_linkedProperty` | string | свойство для синхронизации текущей страницы |
| `idle_linkedProperty` | string | свойство для синхронизации статуса idle |
| `brightness_linkedProperty` | string | свойство для синхронизации яркости |
| `backlight_linkedProperty` | string | свойство для синхронизации включения подсветки |
| `outputX_linkedProperty` | string | свойство для связи с `output1`, `output2` и т.д. |

> [!NOTE]
> Плагин не валидирует схему JSON на уровне модели. Он ожидает, что `pages` существуют, а внутри каждой страницы есть `objects`.

## 3. Формат страницы

Описание страницы:

```json
{
  "comment": "Главная",
  "back": 0,
  "next": 1,
  "prev": 0,
  "objects": [
    {
      "id": 1,
      "obj": "label",
      "x": 10,
      "y": 10,
      "w": 220,
      "h": 40,
      "text": "Заголовок"
    }
  ]
}
```

Поддерживаемые атрибуты страницы, которые плагин отправляет на панель:

- `comment`
- `back`
- `next`
- `prev`

## 4. Формат объекта

Минимально объект должен содержать:

```json
{
  "id": 1,
  "obj": "label"
}
```

Обычно используются и координаты:

```json
{
  "id": 1,
  "obj": "btn",
  "x": 10,
  "y": 10,
  "w": 200,
  "h": 60,
  "text": "Нажми"
}
```

Плагин не ограничивает набор полей объекта: он передаёт на панель практически всё, кроме служебных полей `_linkedMethod`, `_linkedTemplate`, `_linkedScript`, `_command` и `linkedObject`.

## 5. Специальные поля событий

Для событий `up`, `down`, `release`, `long`, `hold`, `changed` можно описывать логику:

| Поле | Что делает |
| --- | --- |
| `<event>_command` | служебная команда плагина, сейчас используются `delete` и `close` |
| `<event>_linkedMethod` | вызывает метод объекта `osysHome` |
| `<event>_linkedTemplate` | открывает шаблон на текущей странице |

Пример:

```json
{
  "id": 10,
  "obj": "btn",
  "x": 10,
  "y": 10,
  "w": 180,
  "h": 60,
  "text": "Открыть popup",
  "up_linkedTemplate": "light_popup",
  "linkedObject": "LightHall"
}
```

## 6. Подстановка значений через `%Object.property%`

### 6.1. Обычная подстановка

Плагин ищет шаблоны:

```text
%Object.property%
```

И заменяет их на текущее значение свойства.

Пример:

```json
{
  "id": 1,
  "obj": "label",
  "x": 10,
  "y": 10,
  "w": 220,
  "h": 40,
  "text": "Температура: %ClimateHall.temperature%C"
}
```

### 6.2. Обратная передача значений

Если объект возвращает `val`, `text` или `color`, плагин может записать значение обратно в `osysHome`.

Пример переключателя:

```json
{
  "id": 2,
  "obj": "switch",
  "x": 10,
  "y": 60,
  "w": 100,
  "h": 50,
  "val": "%LightHall.state%"
}
```

Если событие объекта совпадёт с `value_event`, значение будет записано в `LightHall.state`.

> [!TIP]
> Для `dropdown` и `roller` плагин автоматически использует событие `changed`, даже если глобально `value_event` равно `up`.

## 7. Шаблоны `templates`

Шаблон это именованный массив объектов, который можно:

- встроить в страницу через объект с `obj: "template"`;
- открыть по событию через `<event>_linkedTemplate`.

### 7.1. Встраивание шаблона в страницу

Пример конфигурации:

```json
{
  "pages": [
    {
      "comment": "Главная",
      "objects": [
        {
          "id": 100,
          "obj": "template",
          "template": "sensor_tile",
          "x": 10,
          "y": 10,
          "linkedObject": "ClimateHall"
        }
      ]
    }
  ],
  "templates": {
    "sensor_tile": [
      {
        "id": 1,
        "obj": "obj",
        "w": 220,
        "h": 90,
        "radius": 8,
        "bg_color": "gray"
      },
      {
        "id": 2,
        "obj": "label",
        "x": 12,
        "y": 12,
        "w": 190,
        "h": 24,
        "text": "%.name%"
      },
      {
        "id": 3,
        "obj": "label",
        "x": 12,
        "y": 44,
        "w": 190,
        "h": 24,
        "text": "Температура: %.temperature%C"
      }
    ]
  }
}
```

Что сделает плагин:

- прибавит `id` дочерних объектов шаблона к `id` родителя;
- объединит параметры родительского объекта с первым объектом шаблона;
- заменит `%.temperature%` на `%ClimateHall.temperature%`;
- заменит `%.name%` на имя связанного объекта.

### 7.2. Popup-шаблон по нажатию

```json
{
  "pages": [
    {
      "comment": "Главная",
      "objects": [
        {
          "id": 10,
          "obj": "btn",
          "x": 20,
          "y": 20,
          "w": 180,
          "h": 60,
          "text": "Свет в холле",
          "linkedObject": "LightHall",
          "up_linkedTemplate": "light_popup"
        }
      ]
    }
  ],
  "templates": {
    "light_popup": [
      {
        "id": 200,
        "obj": "obj",
        "x": 40,
        "y": 80,
        "w": 300,
        "h": 180,
        "radius": 12,
        "bg_color": "black"
      },
      {
        "id": 201,
        "obj": "label",
        "x": 60,
        "y": 100,
        "w": 240,
        "h": 30,
        "text": "%.description%"
      },
      {
        "id": 202,
        "obj": "switch",
        "x": 60,
        "y": 145,
        "w": 100,
        "h": 50,
        "val": "%.state%"
      },
      {
        "id": 203,
        "obj": "btn",
        "x": 180,
        "y": 145,
        "w": 120,
        "h": 50,
        "text": "Закрыть",
        "up_command": "close"
      }
    ]
  }
}
```

Примечания:

- `%.description%` подставляет описание объекта из `osysHome`;
- popup рисуется на текущей странице;
- закрытие работает через `close_template`, если событие было открыто как `linkedTemplate`.

## 8. Готовые сценарии конфигурации

### 8.1. Статусная панель с датчиком и кнопкой

```json
{
  "value_event": "up",
  "pages": [
    {
      "comment": "Статус",
      "objects": [
        {
          "id": 1,
          "obj": "label",
          "x": 10,
          "y": 10,
          "w": 220,
          "h": 40,
          "text": "Температура: %ClimateHall.temperature%C"
        },
        {
          "id": 2,
          "obj": "label",
          "x": 10,
          "y": 50,
          "w": 220,
          "h": 40,
          "text": "Влажность: %ClimateHall.humidity%%"
        },
        {
          "id": 3,
          "obj": "btn",
          "x": 10,
          "y": 110,
          "w": 180,
          "h": 60,
          "text": "Переключить свет",
          "up_linkedMethod": "LightHall.toggle"
        }
      ]
    }
  ]
}
```

### 8.2. Управление яркостью через slider

```json
{
  "value_event": "changed",
  "pages": [
    {
      "comment": "Свет",
      "objects": [
        {
          "id": 1,
          "obj": "slider",
          "x": 20,
          "y": 40,
          "w": 280,
          "h": 30,
          "min": 0,
          "max": 100,
          "val": "%DimmerHall.level%"
        },
        {
          "id": 2,
          "obj": "label",
          "x": 20,
          "y": 80,
          "w": 200,
          "h": 30,
          "text": "Яркость: %DimmerHall.level%%"
        }
      ]
    }
  ]
}
```

### 8.3. Синхронизация состояния панели

```json
{
  "page_linkedProperty": "%PanelKitchen.page%",
  "idle_linkedProperty": "%PanelKitchen.idle%",
  "brightness_linkedProperty": "%PanelKitchen.brightness%",
  "backlight_linkedProperty": "%PanelKitchen.backlight%",
  "pages": [
    {
      "comment": "Сервис",
      "objects": [
        {
          "id": 1,
          "obj": "label",
          "x": 10,
          "y": 10,
          "w": 240,
          "h": 30,
          "text": "Страница: %PanelKitchen.page%"
        },
        {
          "id": 2,
          "obj": "label",
          "x": 10,
          "y": 45,
          "w": 240,
          "h": 30,
          "text": "Idle: %PanelKitchen.idle%"
        }
      ]
    }
  ]
}
```

## 9. Что плагин обновляет автоматически

При изменении связанных свойств модуль ищет все упоминания `%Object.property%` и отправляет батч обновлений:

- `page`
- `idle`
- `backlight`
- `outputN`
- `p<page>b<id>.<field>`

Если обновляется несколько полей, плагин отправляет JSON-батч в `command`. Если поле одно, используется `command/<key>`.

## 10. Ограничения и особенности текущей реализации

- `panel_config` должен быть валидным JSON, иначе сохранение или рендер страницы завершится ошибкой.
- Если в корне нет `pages`, часть логики обновления работать не будет.
- Для `templates` предполагается, что имя шаблона существует в конфиге.
- В обработке событий есть ориентация на `val`, `text` и `color`; другие возвращаемые поля отдельно не синхронизируются.
- Команда `close` работает в сценарии шаблона, открытого через `<event>_linkedTemplate`.
- Для `idle=long` плагин рисует чёрный объект-заглушку с фиксированным размером `480x480`.

> [!CAUTION]
> Если объект или свойство из `%Object.property%` не существуют, плагин не сможет корректно связать конфигурацию с моделью `osysHome`. Перед внедрением шаблонов полезно проверить все ссылки вручную.

## 11. Чек-лист перед сохранением конфигурации

- JSON валиден и не содержит комментариев JavaScript;
- каждая страница имеет массив `objects`;
- идентификаторы объектов не конфликтуют внутри страницы;
- у `template`-объекта есть `template` и `linkedObject`, если шаблон использует `%.property%`;
- выбран правильный `value_event`;
- все `%Object.property%` существуют в `osysHome`.
