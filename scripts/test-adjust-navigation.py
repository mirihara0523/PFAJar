import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import ast
from pathlib import Path
from qtpy.QtWidgets import QApplication,QMainWindow,QGraphicsView,QGraphicsEllipseItem,QLineEdit
from qtpy.QtCore import Qt,QEvent,QPointF
from qtpy.QtGui import QKeyEvent,QMouseEvent
app=QApplication([])
tree=ast.parse((Path(__file__).resolve().parents[1]/'py/adjust.py').read_text(encoding='utf-8-sig'))
names={'eventFilter','_sync_navigation_cursor','_set_space_navigation'}
methods=[n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body
         if isinstance(n,ast.FunctionDef) and n.name in names]
cls=ast.ClassDef(name='AnnotationViewer',bases=[ast.Name(id='QMainWindow',ctx=ast.Load())],keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'viewer-methods','exec'))
v=AnnotationViewer(); v.img_view=QGraphicsView(v); v.anno_view=QGraphicsView(v)
v._space_down=False; v._is_panning=False; v._space_pan_active=False; v.is_drawing=False
v._right_pan_pending=False; v._right_pan_start_pos=None; v._pan_last_pos=None
v.paint_dock=object(); v._options_width_ready=False
v._brush_cursor_img=QGraphicsEllipseItem(); v._brush_cursor_anno=QGraphicsEllipseItem()
v.img_view.viewport().setCursor(Qt.CursorShape.CrossCursor)
def key(kind):return QKeyEvent(kind,Qt.Key.Key_Space,Qt.KeyboardModifier.NoModifier,' ')
for view in (v.img_view,v.anno_view):
    assert v.eventFilter(view,key(QEvent.Type.KeyPress))
    assert v._space_down and view.viewport().cursor().shape()==Qt.CursorShape.OpenHandCursor
    assert not v._brush_cursor_img.isVisible()
    v._is_panning=True; v._space_pan_active=True; v._sync_navigation_cursor()
    assert view.viewport().cursor().shape()==Qt.CursorShape.ClosedHandCursor
    v._is_panning=False; v._sync_navigation_cursor()
    assert view.viewport().cursor().shape()==Qt.CursorShape.OpenHandCursor
    v.eventFilter(view,QEvent(QEvent.Type.FocusOut))
    assert not v._space_down
assert v.img_view.viewport().cursor().shape()==Qt.CursorShape.CrossCursor
v.eventFilter(v.img_view,key(QEvent.Type.KeyPress))
v.eventFilter(v.img_view,key(QEvent.Type.KeyRelease))
assert not v._space_down
editor=QLineEdit(v); editor.setText('region'); editor.installEventFilter(v)
returns=[]; editor.returnPressed.connect(lambda:returns.append(True))
QApplication.sendEvent(editor,key(QEvent.Type.KeyPress))
QApplication.sendEvent(editor,key(QEvent.Type.KeyRelease))
assert editor.text()=='region' and len(returns)==1 and not v._space_down
v.is_drawing=True; v.current_delta=0
v._update_parcellation_labels=lambda:None; v._finalize_last_stroke=lambda:None
v._set_space_navigation(True)
assert not v.is_drawing and v.current_delta==1
v.eventFilter(v,QEvent(QEvent.Type.WindowDeactivate))
assert not v._space_down and not v._is_panning

def mouse(kind,x,y,button,buttons):
    pos=QPointF(x,y)
    return QMouseEvent(kind,pos,pos,pos,button,buttons,Qt.KeyboardModifier.NoModifier)

# Right drag pans after the platform drag threshold, without selecting a label.
pans=[]; picks=[]
v._pan_by_pixels=lambda dx,dy:pans.append((dx,dy))
v._select_paint_target_at_view_pos=lambda view,pos:picks.append((pos.x(),pos.y()))
vp=v.img_view.viewport()
assert v.eventFilter(vp,mouse(QEvent.Type.MouseButtonPress,10,10,Qt.MouseButton.RightButton,Qt.MouseButton.RightButton))
assert v._right_pan_pending
assert v.eventFilter(vp,mouse(QEvent.Type.MouseMove,40,10,Qt.MouseButton.NoButton,Qt.MouseButton.RightButton))
assert v._is_panning and pans and not v._right_pan_pending
assert v.eventFilter(vp,mouse(QEvent.Type.MouseButtonRelease,40,10,Qt.MouseButton.RightButton,Qt.MouseButton.NoButton))
assert not v._is_panning and not picks

# A short right click retains target selection behavior.
assert v.eventFilter(vp,mouse(QEvent.Type.MouseButtonPress,12,14,Qt.MouseButton.RightButton,Qt.MouseButton.RightButton))
assert v.eventFilter(vp,mouse(QEvent.Type.MouseButtonRelease,12,14,Qt.MouseButton.RightButton,Qt.MouseButton.NoButton))
assert picks==[(12,14)]
print('PASS hand cursors, release/focus loss, stroke finalization, text Space-to-Return')
