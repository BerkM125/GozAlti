// ***********************************************************************
// Project          : Travelers.UI
// Author           : Graim George
// Created          : 03-03-2015
//
// Last Modified By : Graim George
// Last Modified On : 12-12-2016
// ***********************************************************************
// <summary>Utility helper methods</summary>
// ***********************************************************************
var utils = {
    'config': {
        //Wowsa url used for Camera streams
        'wowsaUrl': ''
    },

    /*Gets map-key from web.confg */
    'getMapKey': function () {
       return ($.ajax({
            url: index.config.appRoot + 'api/Map/MapKey',
            type: "GET",
            async: false,
            success: function (data) {
                return data;
            },
            error: function () { return null; }
        })).responseJSON;

    },
    /*Get wowsa url from web.config */
    'getWowsaUrl': function () {
        if (utils.config.wowsaUrl == '')
        {
            utils.config.wowsaUrl = ($.ajax({
                url: index.config.appRoot + 'api/Map/WowsaUrl',
                type: "GET",
                async: false,
                success: function (data) {
                    return data;
                },
                error: function () { return null; }
            })).responseJSON;
        }

        return utils.config.wowsaUrl;

    },
    /*Check if address is within Seattle boundary */
    'isAddressWithinValidBounds': function (lat, lng) {

        var result = false;

        $.ajax({
            url: index.config.appRoot + "api/Map/IsAddressWithinValidBounds?lat=" + lat + '&lng=' + lng,
            type: "Get",
            async: false,
            success: function (data) {
                result = data;
            },
            error: function (msg) { return false; }
        });

        return result;
    },
    /*Get website application path */
    'getAppPath': function()
    {
        return location.protocol + '//' + location.host + $('#site-app-path').val() + '/';
        
    },
    /*Loads url content on a target element */
    'loadPartialPage': function(path, target){
        $.ajax({
            url: path,
            dataType: "html",
            async: false,
            success: function (data) {
                $("#" + target).html(data);
            },
            error: function (msg)
            {
                console.log(msg);
            }
        });
    },
    /*Set element text */
    'setControlText': function (id, value) {
        var ctl = $("#" + id);
        ctl.show();
        ctl.text(value);
    },
    /*Set element visibility */
    'setControlVisibility': function (id, flag) {
        var ctl = $("#" + id);
        if (flag) {
            ctl.show();
        }
        else {
            ctl.hide();
        }
    },
    'createNeighborhoodLabel': function(name, lat, lng){
        var infobox = new Microsoft.Maps.Infobox(new Microsoft.Maps.Location(lat, lng), { visible: true, htmlContent: '<div style="color:black;font-weight:bold;font-size:0.7em;white-space: nowrap;">' + name + '</div>' });
        return infobox;
    },
    /*Creates shape layer using Entity collection */
    'createShapeLayer': function (name, visible, zoomRangeMin, zoomRangeMax) {
        //var map = mapmain.config.map;
        var layer = new Microsoft.Maps.Layer();
        //layer.setVisible(visible);
        //map.layers.insert(layer);
        return layer;
    },
    /*Create pushpin with coordinates */
    'createPushpin': function (coordinate, iconUrl, iconHeight, iconWidth, hightlight) {
        var pin = null;
        if (coordinate && coordinate.length == 2) {
            pin = new Microsoft.Maps.Pushpin(new Microsoft.Maps.Location(coordinate[0], coordinate[1]), { icon: iconUrl, width: iconWidth, height: iconHeight, cursor: 'pointer' });

            if (hightlight) {
                Microsoft.Maps.Events.addHandler(pin, 'mouseover', function (e) {
                    e.target.setOptions({ icon: iconUrl.replace('.png', '_Selected.png') });
                });

                Microsoft.Maps.Events.addHandler(pin, 'mouseout', function (e) {
                    e.target.setOptions({ icon: iconUrl });
                });
            }
        }
        return pin;
    },

    /*Create line shape using coordinate collection */
    'createLineShape': function (coordinates) {

        //var coordinates = coordinatesStr.split(' ');
        var shape = null;
        if (coordinates != null && coordinates.length > 1 && coordinates.length % 2 == 0) {
            var list = new Array();
            for (var i = 0; i < coordinates.length; i = i + 2) {
                list.push(new Microsoft.Maps.Location(coordinates[i], coordinates[i + 1]));
            }
            shape = new Microsoft.Maps.Polyline(list, { strokeColor: new Microsoft.Maps.Color(200, 77, 255, 255), strokeThickness: 5, strokeDashArray: "10 3" });           
        }
        return shape;
    },

    /*Gets the camera icon for clusted and non-clustered */
    'getCameraFeatureIconUrl' : function (cameras) {
        return cameras != null && cameras.length > 1 ? SDOT_CLUSTERED_TRAFFIC_CAMERA_ICON_PATH : SDOT_TRAFFIC_CAMERA_ICON_PATH;
    },
    /*Gets the latest camera image for sdot and wsdot */
    'getCameraCurrentImageUrl': function (cameraType, imageName) {
        var url = "";
        if (imageName) {
            if (cameraType == "sdot")
                url = SDOT_TRAFFIC_CAMERA_CURRENT_IMAGE_PATH + imageName;
            else if (cameraType == "wsdot")
                url = WSDOT_TRAFFIC_CAMERA_CURRENT_IMAGE_PATH + imageName;
            else if (cameraType == "port")
                url = PORT_TRAFFIC_CAMERA_CURRENT_IMAGE_PATH + imageName;
        }
        return url;
    },
    /* Get event/incident category type from the feature class*/
    'getEventIncidentFeatureCategoryType' : function (feature) {
        var categoryType = 0;
        if (feature) {
            if (feature.Incidents)
                categoryType = 1;
            else if (feature.Events)
                categoryType = 2;
        }
        return categoryType;
    },

    /*Gets bridge image */
    'getBridgeFeatureIconUrl': function (bridge) {
        return (bridge.Status == "Open") ? SDOT_BRIDGE_OPENED_ICON_PATH : SDOT_BRIDGE_CLOSED_ICON_PATH;
    },

    /*Get DMS image */
    'getDMSSignFeatureIconUrl': function (sign) {
        return (sign.Status == "On") ? SDOT_DMS_SIGN_ON_ICON_PATH : SDOT_DMS_SIGN_OFF_ICON_PATH;
    },
    /*Get RR Xing image */
    'getRRCrossingFeatureIconUrl': function (rrCrossing) {
        return (rrCrossing.Status == "Open") ? SDOT_CROSSING_OPENED_ICON_PATH : SDOT_CROSSING_CLOSED_ICON_PATH;
    },
    /*Get incident image */
    'getIncidentFeatureIconUrl' : function (incidents) {
        var iconPath = incidents == null || incidents.length < 2 ? INCIDENT_ICON_PATH : INCIDENT_CLUSTERED_ICON_PATH;
        return iconPath;
    },
    /*Get event image */
    'getEventFeatureIconUrl': function (events) {
        var iconPath = events == null || events.length < 2 ? EVENT_ICON_PATH : EVENT_CLUSTERED_ICON_PATH;
        return iconPath;
    },
    /*Get travel time url */
    'getTravelTimeFeatureIconUrl': function () {
        var iconPath = TRAVEL_TIME_ICON_PATH;
        return iconPath;
    },
    /*Popup carousel window in draggable mode */
    'showPopupCarousel' : function(flag, type)
    {
        
        if (flag) {
            //show disclaimer warning
            if (type == 'camera') {
                $('#greeting-text').html('<marquee direction="left" style="color:red;width:300px;">The CCTV system and associated data are intended for traffic monitoring or traffic management and for no other purpose.</marquee>');
            }

            $('#popup-carousel').show();
            $('#popup-carousel').attr('data-type', type);
            //$('#popup-carousel').draggable();       
        }
        else
        {
            var popupType = $('#popup-carousel').attr('data-type');           
            //alert(popupType);
            if (popupType == type) {
                $('#popup-carousel').hide();

            }
            //when the carousel is closed, remove the related shapes from map
            utils.removeTravelTimeLink();
            $('#greeting-text').html('Welcome ' + user.config.greetingName + '!');
        }        
    },
    /*Shows custom traffic legend */
    'showTrafficLegend': function (flag) {
        if (flag) {
            $('#traffic-legend').show();        
        }
        else {
            $('#traffic-legend').hide();
        }
    },
    /*Shows or hides carousel arrow */
    'showHidePopUpCarouselArrow': function(flag)
    {
        var value = (flag == "show")? "": "none";
        $('.right.carousel-control, .left.carousel-control').css('display', value);
    },
    /*Format data time for events and incidents */
    'formatEventIncidentDateTime': function (dateStr) {

        var date = new Date(dateStr);

        var formattedDate = "";
        var month = date.getMonth() + 1;
        var day = date.getDate();
        var year = date.getFullYear();
        var hour = date.getHours();
        var minute = date.getMinutes();
        minute = minute < 10 ? "0" + minute : minute;
        var amPm = hour > 11 ? "PM" : "AM";
        hour = hour == 0 ? 24 : hour;
        hour = hour > 12 ? hour - 12 : hour;
        formattedDate = (month + "/" + day + "/" + year + " " + hour + ":" + minute + " " + amPm);
        return formattedDate;
    },
    /*Format travel time for travel routes */
    'formattedTravelTime': function(status, time, isDms)
    {
        var value = "";
        switch (status.toString()) {          
            case "1":
                {
                    if (isDms) {
                        value = time + " MIN.";
                    }
                    else {
                        timeInt = parseInt(time);
                        value = timeInt < 30 ? "1 MIN." : (Math.round(timeInt / 60)).toString(10) + " MIN.";
                    }
                    break;
                }
            default:
                {
                    value = "N/A";
                    break;
                }
        }

        return value;
    },
    /*Helper method to remove an item from array collection */
    'removeAlertItemFromArray':function (array, item)
    {
        for (var i = array.length - 1; i >= 0; i--)
            if (array[i].Id === item.Id) {
                array.splice(i, 1);
                break;
            }
    },

    /*Remove camera item from array */
    'removeCameraItemFromArray': function (array, item)
    {
        for (var i = array.length - 1; i >= 0; i--)
            if (array[i].Id === item.Id) {
                array.splice(i, 1);
                break;
            }
    },
    /*Remove travel link item from array */
    'removeTravelLinkFromArray':function (array, item)
    {
        for (var i = array.length - 1; i >= 0; i--)
            if (array[i].LinkID === item.LinkID) {
                array.splice(i, 1);
                break;
            }
    },
    /*Remove bridge item from array */
    'removeBridgeItemFromArray': function (array, item) {
        for (var i = array.length - 1; i >= 0; i--)
            if (array[i].BridgeID === item.BridgeID) {
                array.splice(i, 1);
                break;
            }
    },
    /*Remove RR Xing from array */
    'removeRRCrossingItemFromArray': function (array, item) {
        for (var i = array.length - 1; i >= 0; i--)
            if (array[i].RRCrossingID === item.RRCrossingID) {
                array.splice(i, 1);
                break;
            }
    },
    /*Remove neighborhood from event incident array */
    'removeEventIncidentNeighborhoodFromArray': function (array, item) {
        for (var i = array.length - 1; i >= 0; i--)
            if (array[i] == item) {
                array.splice(i, 1);
                break;
            }
    },
    /*Return link object from link id */
    'getSiteTravelLinkByLinkId': function (siteLinks, linkId) {

        var linkObj = null;
        for (var i = siteLinks.length - 1; i >= 0; i--)
            if (siteLinks[i].LinkID == linkId) {
                linkObj = siteLinks[i];
                break;
            }
        return linkObj;
    },
    /*Shows profile saved message */
    'showProfileSaveMessage' : function(status)
    {
        if (!status)
        {
            $("#profile-save-result1, #profile-save-result2").html("Save Failed. Try Later...")
        }

        $("#profile-save-result1, #profile-save-result2").show();
        
        $("#profile-save-result1, #profile-save-result2").fadeOut(5000, 'swing');
    },
    /*Gets current timestamp */
    'getTimeStamp': function()
    {
        return new Date().getTime();
    },
    /*Compares two objects based on description field */
    'descriptionSortComparer' : function(a, b)
    {
        var aName = a.Description.toLowerCase();
        var bName = b.Description.toLowerCase();
        return ((aName < bName) ? -1 : ((aName > bName) ? 1 : 0));
    },
    /*Compares two objects based on Name field */
    'displayNameSortComparer': function (a, b) {
        var aName = a.DisplayName.toLowerCase();
        var bName = b.DisplayName.toLowerCase();
        return ((aName < bName) ? -1 : ((aName > bName) ? 1 : 0));
    },
    /*Sort two objects based on Name field */
    'nameSortComparer': function (a, b) {
        var aName = a.Name.toLowerCase();
        var bName = b.Name.toLowerCase();
        return ((aName < bName) ? -1 : ((aName > bName) ? 1 : 0));
    },
    /*Sort two objects based on commute source site name */
    'commuteSortComparer': function (a, b) {
        var aName = a.SrcSiteName.toLowerCase() + a.LinkDisplayName.toLowerCase();
        var bName = b.SrcSiteName.toLowerCase() + b.LinkDisplayName.toLowerCase();
        return ((aName < bName) ? -1 : ((aName > bName) ? 1 : 0));
    },
    /*Remove travel time link from shape collection */
    'removeTravelTimeLink': function()
    {
        if (mapmain.config.travelTimeLink != null) {
            mapmain.config.map.layers.remove(mapmain.config.travelTimeLink);
        }

        if (mapmain.config.currentTravelTimeInfoBox != null)
        {
            mapmain.config.currentTravelTimeInfoBox.setMap(null);
        }
    },
    /*Validates email format */
    'validateEmail': function(email)
    {
        var emailReg = /^([\w-\.]+@([\w-]+\.)+[\w-]{2,4})?$/;
        return emailReg.test(email);
    },
    /*Validates emails collection */
    'validateEmails': function(emails)
    {
        if (emails != "") {
            var res = emails.split(",");
            for (i = 0; i < res.length; i++)
                if (!utils.validateEmail(res[i])) return false;
        }
        return true;
    },
    /*Check if the string ends with a substring */
    'endsWith': function (str, suffix) {
        return str.indexOf(suffix, str.length - suffix.length) !== -1;
    }
};
/*Extension method for replaceAll */
String.prototype.replaceAll = function (search, replacement) {
    var target = this;
    return target.replace(new RegExp(search, 'g'), replacement);
};

/* check if an element exists in array using a comparer function
 comparer : function(currentElement)*/
Array.prototype.inArray = function (comparer) {
    for (var i = 0; i < this.length; i++) {
        if (comparer(this[i])) return true;
    }
    return false;
};

/* adds an element to the array if it does not already exist using a comparer 
   function*/
Array.prototype.pushIfNotExist = function (element, comparer) {
    if (!this.inArray(comparer)) {
        this.push(element);
    }
};

/* check if the element already already exist using a comparer 
  function */
Array.prototype.doesExist = function (element, comparer) {
    if (this.inArray(comparer)) {
        return true;
    }
    else
    { return false;}
};
/*Extension method for array remove */
Array.prototype.remove = function () {
    var what, a = arguments, L = a.length, ax;
    while (L && this.length) {
        what = a[--L];
        while ((ax = this.indexOf(what)) !== -1) {
            this.splice(ax, 1);
        }
    }
    return this;
};