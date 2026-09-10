# Тёмная тема в Qt: почему белый текст на белом фоне и как это чинить

Заметка по итогам разбора настоящей ошибки в SubtitleForge Studio. Годится
для любой программы на Qt/PySide/PyQt со своей темой оформления.

---

## Симптом

Часть окна остаётся светлой под тёмной темой: вкладки, кнопки, списки — белые,
а текст на них тоже белый и потому нечитаем.

Главная примета: **у одних людей всё выглядит правильно, у других сломано**, на
одном и том же файле программы. Отсюда обманчивые версии про видеокарту,
драйверы и версию Windows.

---

## Причина

Цвета в Qt приходят из **двух независимых источников**:

1. ваша таблица стилей (`setStyleSheet`);
2. палитра Qt (`QPalette`), которая по умолчанию берётся **у системы**.

А третий участник — **стиль** (`QStyle`) — решает, кто из двух победит для
каждого виджета. То, что стиль рисует сам, он красит из палитры и вашу таблицу
стилей игнорирует.

Стиль по умолчанию зависит от операционной системы:

| Система | Стиль по умолчанию |
|---|---|
| Windows 11 | `windows11` |
| Windows 10 | `windowsvista` |
| Linux | `Fusion` |
| macOS | `macOS` |

Дальше просто. Тёмная тема задаёт белый текст через таблицу стилей. Фон вкладки
рисует системный стиль и берёт его из системной палитры. У человека с тёмной
темой Windows системный фон тоже тёмный — цвета случайно сходятся, всё выглядит
верно. У человека со светлой темой Windows фон приходит светлый — получается
белое на белом.

**Ни видеокарта, ни версия Windows сами по себе ни при чём.** Версия влияет
только через выбор стиля: `windowsvista` рисует больше своего, чем `windows11`,
поэтому на десятке ломается заметнее.

---

## Починка: три шага, все обязательны

Ни один из них по отдельности не решает задачу.

```python
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

# ── 1. Стиль задаём явно, до создания виджетов ─────────────────────────
# Fusion — единственный, который одинаков на всех системах и слушается
# и таблицу стилей, и палитру.
app.setStyle("Fusion")

# ── 2. Палитра — из своих цветов, а не из системы ──────────────────────
palette = QPalette()
palette.setColor(QPalette.ColorRole.Window,          QColor(bg))
palette.setColor(QPalette.ColorRole.WindowText,      QColor(fg))
palette.setColor(QPalette.ColorRole.Base,            QColor(input_bg))
palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(row_bg))
palette.setColor(QPalette.ColorRole.Text,            QColor(fg))
palette.setColor(QPalette.ColorRole.Button,          QColor(button_bg))
palette.setColor(QPalette.ColorRole.ButtonText,      QColor(fg))
palette.setColor(QPalette.ColorRole.Highlight,       QColor(accent))
palette.setColor(QPalette.ColorRole.HighlightedText, QColor(bg))
palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor(tooltip_bg))
palette.setColor(QPalette.ColorRole.ToolTipText,     QColor(fg))
palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(muted))
palette.setColor(QPalette.ColorRole.Link,            QColor(accent))

# Недоступные элементы — отдельной группой. Их цвет берётся ТОЛЬКО из
# палитры: не задать — на тёмной теме получится серое по серому.
for role in (QPalette.ColorRole.WindowText,
             QPalette.ColorRole.Text,
             QPalette.ColorRole.ButtonText):
    palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(muted))

app.setPalette(palette)

# ── 3. Таблица стилей, покрывающая всё, что иначе рисует стиль ─────────
app.setStyleSheet(qss)
```

---

## Что покрыть в таблице стилей

Обычно люди пишут правила для `QWidget`, полей ввода и меню — и на этом
останавливаются. Ниже то, что чаще всего остаётся системному стилю и потому
ломается. Список можно скопировать в тест как обязательный.

```
QDialog, QMessageBox

QTabWidget::pane
QTabWidget::tab-bar          /* иначе первая вкладка уезжает за край */
QTabBar::tab
QTabBar::tab:selected
QTabBar::tab:hover:!selected

QPushButton
QPushButton:hover
QPushButton:pressed
QPushButton:default
QPushButton:disabled

QCheckBox::indicator
QRadioButton::indicator
QCheckBox::indicator:checked
QRadioButton::indicator:checked

QListView, QListWidget, QTreeView, QTreeWidget
QListView::item:hover, QTreeView::item:hover

QComboBox QAbstractItemView   /* выпадающий список — отдельное окно! */
QComboBox::drop-down

QSpinBox, QDoubleSpinBox
QSpinBox::up-button, QSpinBox::down-button

QToolTip
QProgressBar, QProgressBar::chunk
QSlider::groove:horizontal, QSlider::sub-page:horizontal, QSlider::handle:horizontal
QDockWidget, QDockWidget::title
QHeaderView::section
QScrollBar, QScrollBar::handle
QGroupBox, QGroupBox::title
QStatusBar, QToolBar, QToolButton
QMenuBar, QMenuBar::item, QMenu, QMenu::item
```

---

## Четыре ловушки

### 1. Всплывающие окна не наследуют общий фон

Выпадающий список комбобокса — **отдельное окно верхнего уровня**. Правило
`QWidget { background: … }` на него не распространяется, нужно именно:

```css
QComboBox QAbstractItemView { background: …; color: …; }
```

Так же ведут себя подсказки (`QToolTip`) и меню (`QMenu`).

### 2. Стрелки исчезают, а треугольник из рамок не работает

Как только у виджета появляется **хоть одно** правило таблицы стилей, Qt
перестаёт рисовать его субэлементы системным стилем — и стрелки комбобокса и
счётчика просто пропадают. Поле становится неотличимо от обычного ввода.

Приём с рамками, которым в CSS делают треугольник, Qt рисует **прямоугольником**
— проверено:

```css
/* НЕ РАБОТАЕТ в Qt: получится квадратик */
QComboBox::down-arrow {
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #888;
}
```

Рабочий путь — нарисовать картинки нужного цвета и подставить их:

```python
def arrow_file(color: str, direction: str) -> str:
    """Рисует стрелку 9×6 и возвращает путь для url(...)."""
    from PySide6.QtCore import QPoint, QStandardPaths, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygon

    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    folder = Path(root) / "arrows"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{direction}-{color.lstrip('#')}.png"

    if not path.is_file():                       # цвет в имени — кэш сам себя
        points = ((0, 0), (9, 0), (4, 6)) if direction == "down" \
            else ((0, 6), (9, 6), (4, 0))
        pixmap = QPixmap(9, 6)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        painter.end()
        pixmap.save(str(path))
    return path.as_posix()                       # прямые слэши для url()
```

```css
QComboBox::down-arrow { image: url(<путь>); width: 9px; height: 6px; }
```

### 3. Проверить «какой сейчас стиль» опросом нельзя

После `setStyleSheet` Qt подменяет стиль обёрткой `QStyleSheetStyle`, у которой
`objectName()` пустой, а метода `baseStyle()` в PySide6 нет:

```python
app.setStyle("Fusion")
app.style().objectName()          # 'fusion'
app.setStyleSheet("...")
app.style().objectName()          # '' ← пусто
app.style().metaObject().className()   # 'QStyleSheetStyle'
```

Поэтому условие вида `if app.style().objectName() != "fusion": app.setStyle(...)`
всегда истинно и пересоздаёт стиль при каждой смене темы. Заведите свой флаг:

```python
if not app.property("style_fixed"):
    app.setStyle("Fusion")
    app.setProperty("style_fixed", True)
```

### 4. Недоступные элементы красятся только палитрой

Ни одно правило таблицы стилей не помогает, если не задана группа
`QPalette.ColorGroup.Disabled` — Qt вычислит цвет от системного и на тёмной
теме даст серое по серому.

---

## Как поймать это тестом, а не жалобой пользователя

Три проверки, все дешёвые и быстрые.

### Рендер окна при чужой системной палитре

Самая ценная: она воспроизводит ровно ту машину, где ломается.

```python
def light_share(widget) -> float:
    """Доля светлых пикселей, в процентах."""
    image = QImage(widget.size(), QImage.Format_RGB32)
    widget.render(image)                       # show() не нужен
    light = total = 0
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            c = QColor(image.pixel(x, y))
            total += 1
            if 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() > 170:
                light += 1
    return 100.0 * light / max(1, total)

def test_dark_theme_paints_dark(qapp, settings):
    apply_theme(qapp, settings)                # тёмная
    window = sample_window()                   # вкладки, кнопки, списки, поля
    assert light_share(window) < 3.0
```

Замер на настоящих стилях Windows в моём случае: **8,2 %** светлых пикселей до
починки против **0,3 %** после.

Оговорка: воспроизвести неисправность удаётся только на реальной платформе —
под `QT_QPA_PLATFORM=offscreen` нативные стили Windows недоступны, там всегда
Fusion, и тест мимо проблемы. Такой тест всё равно полезен (ловит светлую тему
вместо тёмной), но диагностику придётся делать в обычном режиме.

### Покрытие таблицы стилей

Ловит удаление правила при рефакторинге — у меня поймал сразу же, я случайно
вырезал `QComboBox QAbstractItemView`.

```python
REQUIRED = ("QTabBar::tab", "QPushButton", "QCheckBox::indicator",
            "QListView", "QToolTip", "QProgressBar", "QDockWidget::title",
            "QComboBox QAbstractItemView", "QDialog")

def test_every_troublesome_widget_is_styled():
    qss = build_qss(DARK)
    missing = [name for name in REQUIRED if name not in qss]
    assert not missing, f"без правил остались: {missing}"
```

### Контраст по WCAG для каждой темы

```python
def contrast(first: str, second: str) -> float:
    a, b = luminance(first), luminance(second)      # относительная яркость
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)

# основной текст к фону
assert contrast(palette.text_primary, palette.bg_base) >= 4.5
# приглушённый текст
assert contrast(palette.text_muted, palette.bg_base) >= 3.0
# текст на выделении — про него забывают чаще всего
assert contrast(palette.text_primary, palette.accent_muted) >= 3.0
```

**Проверьте, что тесты ловят возврат неисправности.** Уберите `setPalette`,
уберите `setStyle`, вырежьте блок правил — и убедитесь, что тесты падают. Тест,
который не падает на сломанном коде, только создаёт ощущение защиты.

---

## Смежная ловушка: чужой текст как разметка

Из того же разбора, другой механизм, но бьёт по тем же местам.

`QLabel` и `QToolTip` **сами решают**, что им дали: обычный текст или HTML. Если
строка похожа на разметку, она будет отрисована как разметка. Проверено: `QLabel`
с `<img src="…">` принимает размеры картинки, то есть файл действительно
загружается.

Для программы, показывающей чужие данные (текст из открытого файла, имя из чужого
документа), это не только испорченный вид, но и утечка: путь вида
`\\сервер\общий\точка.png` заставит Windows пойти на этот сервер и представиться
учётной записью пользователя.

```python
import html
from PySide6.QtGui import Qt

def plain_tooltip(text: str) -> str:
    """Готовит чужой текст к показу, ничего не выполняя."""
    if not text or not Qt.mightBeRichText(text):
        return text                                  # быстрая ветка
    return f"<div style='white-space: pre-wrap'>{html.escape(text)}</div>"
```

И мелочь оттуда же: **амперсанд в тексте пункта меню Qt считает мнемоникой**.
Имя «Иван & Марья» покажется как «Иван  Марья» с подчёркнутым пробелом. Для
данных из файла удваивайте: `text.replace("&", "&&")`.

---

## Если программа не на Qt

Принцип общий: **если у фреймворка есть «системная тема» и «ваши стили» — это
два источника, и они разъедутся у пользователя с другой темой ОС.**

Проверять надо не «выглядит ли хорошо у меня», а «что будет при противоположной
системной теме». Достаточно один раз переключить тему Windows и пройтись по
окнам.

Где искать тот же класс ошибки:

* **WinForms** — `Application.VisualStyleState`, `SystemColors.*`. Контролы,
  рисуемые системой, ваши цвета игнорируют.
* **WPF** — `SystemColors.*Brush` внутри стандартных шаблонов; тема меняется, а
  шаблон продолжает тянуть системную кисть.
* **Electron / веб** — `prefers-color-scheme`, свойство `color-scheme` и
  системные цвета элементов форм (`accent-color`). Нативные `<select>`,
  скроллбары и автозаполнение красятся системой.
* **GTK** — тема пользователя поверх вашего CSS; приоритеты провайдеров стилей.
* **Tkinter/ttk** — тема `ttk.Style` и платформенные темы (`vista`, `aqua`),
  часть опций на них просто игнорируется.

---

## Короткий чек-лист

- [ ] Стиль задан явно (`Fusion`), до создания виджетов
- [ ] `QPalette` собрана из своих цветов и применена к приложению
- [ ] Группа `Disabled` в палитре заполнена
- [ ] В таблице стилей есть правила для вкладок, кнопок, флажков, списков,
      подсказок, прогресса, ползунков, заголовков панелей
- [ ] Выпадающий список комбобокса покрыт отдельным правилом
- [ ] Стрелки комбобокса и счётчика на месте (картинками, не рамками)
- [ ] Факт установки стиля проверяется флагом, а не `objectName()`
- [ ] Чужой текст экранируется перед показом в подсказке или `QLabel`
- [ ] Есть тест на долю светлых пикселей и на контраст
- [ ] Тесты проверены возвратом неисправности
- [ ] Пройдено глазами при **противоположной** теме системы
